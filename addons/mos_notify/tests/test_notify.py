from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestSupplierNotices(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        cls.cashier = new_test_user(cls.env, login='xnotifycash', groups='mos_payables.group_mos_cashier')
        cls.cashier.mos_site_ids = [Command.set(cls.site.ids)]
        cls.account = cls.env['mos.paying.account'].create({'name': 'Notice bank', 'kind': 'bank'})
        cls.s1.email = 'supplier.one@example.test'

    def _on(self, value='True'):
        self.env['ir.config_parameter'].sudo().set_str('mos_notify.supplier_notices', value)

    def _notices(self, kind):
        return self.env['mos.supplier.notice'].search([('kind', '=', kind)])

    def _visit(self, order, plate):
        return self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': order.partner_id.id})

    def test_nothing_is_queued_while_notices_are_off(self):
        self._on('False')
        self._receive(self._order(), 'NT-001')
        self.assertFalse(self.env['mos.supplier.notice'].search([]))

    def test_arrival_receipt_notices_carry_the_deduction_breakdown(self):
        self._on()
        order = self._order()
        visit = self._receive(order, 'NT-002')
        arrival = self._notices('arrival')
        self.assertEqual(len(arrival), 1)
        self.assertEqual((arrival.partner_id, arrival.state, arrival.mail_id.email_to), (self.s1, 'queued', 'supplier.one@example.test'))
        self.assertIn('NT-002', arrival.mail_id.subject)
        receipt = self._notices('receipt')
        self.assertEqual(len(receipt), 1)
        body = receipt.mail_id.body_html
        self.assertIn('Payable quantity', body)
        self.assertIn('Quality deduction', body)
        self.assertIn(f'{visit.inspection_id.payable_qty:,.1f}', body)

    def test_rejection_note_lists_the_results(self):
        self._on()
        order = self._order()
        visit = self._visit(order, 'NT-003')
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(50000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(50000.0, 15000.0)
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(self.line, 20.0)  # moisture far above the 14 allowed
        insp.action_submit_results()
        insp.with_user(self.labmgr).decide('reject', note='Wet wheat')
        notice = self._notices('rejection')
        self.assertEqual(len(notice), 1)
        self.assertIn('FAILED', notice.mail_id.body_html)
        self.assertIn('Moisture', notice.mail_id.body_html)
        self.assertIn('Wet wheat', notice.mail_id.body_html)

    def test_payment_advice_names_the_invoices_and_amount(self):
        self._on()
        order = self._order()
        self._receive(order, 'NT-004')
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create({'supplier_id': self.s1.id, 'supplier_ref': 'NT-INV-1', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': 35000.0, 'unit_price': 20.0})
        invoice.with_user(self.apclerk).action_match()
        voucher = invoice.voucher_line_ids.voucher_id
        voucher.with_user(self.apclerk).action_submit()
        payment = self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': voucher.net_amount, 'account_id': self.account.id, 'reference': 'TRX-1'})
        notice = self._notices('payment')
        self.assertEqual(len(notice), 1)
        body = notice.mail_id.body_html
        self.assertIn(payment.name, notice.subject)
        self.assertIn(invoice.name, body)
        self.assertIn(f'{payment.amount:,.2f}', body)
        self.assertIn('TRX-1', body)

    def test_a_supplier_without_an_address_is_recorded_not_forgotten(self):
        self._on()
        self.s2.email = False
        order = self._order(supplier=self.s2)
        self._visit(order, 'NT-005')
        notice = self._notices('arrival').filtered(lambda n: n.partner_id == self.s2)
        self.assertEqual((notice.state, bool(notice.mail_id)), ('no_email', False))

    def test_notice_state_follows_the_mail(self):
        self._on()
        self._visit(self._order(), 'NT-006')
        notice = self._notices('arrival')
        self.assertEqual(notice.state, 'queued')
        notice.mail_id.write({'state': 'sent'})
        self.assertEqual(notice.state, 'sent')
        notice.mail_id.write({'state': 'exception'})
        self.assertEqual(notice.state, 'failed')
