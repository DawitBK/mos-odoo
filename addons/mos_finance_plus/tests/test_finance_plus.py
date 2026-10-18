import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestFinancePlus(TransactionCase):
    """Self-billing, statements, GRNI, tax registers, the journal export and payment runs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLF2', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('fpguard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('fpverifier', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('fpanalyst', 'mos_lab.group_mos_lab_analyst')
        cls.labmgr = user('fplabmgr', 'mos_lab.group_mos_lab_manager')
        cls.store = user('fpstore', 'mos_inbound.group_mos_storekeeper')
        cls.buyer = user('fpbuyer', 'purchase.group_purchase_user,mos_core.group_mos_user')
        cls.apclerk = user('fpap', 'mos_payables.group_mos_ap_clerk')
        cls.cashier = user('fpcash', 'mos_payables.group_mos_cashier')
        cls.cashier2 = user('fpcash2', 'mos_payables.group_mos_cashier')
        cls.fin = user('fpfin', 'mos_approvals.group_mos_finance_manager')
        kg = env.ref('uom.product_uom_kgm')
        cls.wheat = env['product.product'].create({'name': 'Finance wheat', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 20.0})
        cls.supplier = env['res.partner'].create({'name': 'Billing Supplier', 'supplier_rank': 1, 'vat': 'TIN-0001', 'mos_vat_pct': 15.0, 'mos_wht_pct': 2.0})
        cls.farmer = env['res.partner'].create({'name': 'Farmer Abebe', 'supplier_rank': 1, 'mos_self_billing': True, 'mos_vat_pct': 15.0, 'mos_wht_pct': 0.0})
        spec = env['mos.lab.spec'].create({'product_id': cls.wheat.id, 'line_ids': [Command.create({'name': 'Moisture', 'min_value': 0, 'max_value': 14})]})
        spec.action_approve()
        cls.line = spec.line_ids
        cls.account = env['mos.paying.account'].create({'name': 'Finance bank', 'kind': 'bank'})

    # -- helpers -----------------------------------------------------------------------------------------------------
    def _receipt(self, supplier, plate, qty=40000.0):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': supplier.id, 'order_line': [Command.create({'product_id': self.wheat.id, 'product_qty': qty + 5000, 'price_unit': 20.0})]}
        )
        order.with_user(self.buyer).button_confirm()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': supplier.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(qty + 15000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(qty + 15000.0, 15000.0)
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(self.line, 12.0)
        insp.action_submit_results()
        insp.with_user(self.labmgr).decide('accept')
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        return order, visit

    def _approved_voucher(self, plate, qty=40000.0, ref=None, clerk=None):
        clerk = clerk or self.apclerk
        order, visit = self._receipt(self.supplier, plate, qty)
        invoice = self.env['mos.supplier.invoice'].with_user(clerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': ref or f'INV-{plate}', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': qty, 'unit_price': 20.0}
        )
        invoice.with_user(clerk).action_match()
        voucher = invoice.voucher_line_ids.voucher_id
        voucher.with_user(clerk).action_submit()
        self.assertEqual(voucher.state, 'approved')
        return voucher, invoice

    @staticmethod
    def _raw(value):
        return value.content if hasattr(value, 'content') else (base64.b64decode(value) if not value.startswith(b'Date,') else value)

    # -- self-billing --------------------------------------------------------------------------------------------------
    def test_a_self_billing_supplier_gets_a_voucher_from_the_receipt(self):
        order, visit = self._receipt(self.farmer, 'F-1')
        invoice = visit.self_bill_id
        self.assertTrue(invoice.self_billed)
        self.assertEqual((invoice.supplier_ref, invoice.qty, invoice.unit_price), (f'SB-{visit.name}', 40000.0, 20.0))
        self.assertEqual(invoice.vat_pct, 0.0, 'no input VAT for a supplier without a VAT registration')
        self.assertEqual((invoice.match_state, invoice.state), ('matched', 'payable'))
        self.assertEqual(invoice.voucher_line_ids.voucher_id.state, 'draft', 'the payment is drafted from it')
        self.assertEqual(visit._self_bill(), invoice, 'once per receipt')
        self.assertEqual(self.env['mos.supplier.invoice'].search_count([('source_visit_id', '=', visit.id)]), 1)
        registered = self.env['res.partner'].create({'name': 'Registered farmer co-op', 'supplier_rank': 1, 'mos_self_billing': True, 'vat': 'TIN-777', 'mos_vat_pct': 15.0, 'mos_wht_pct': 2.0})
        _o, visit2 = self._receipt(registered, 'F-2')
        self.assertEqual(visit2.self_bill_id.vat_pct, 15.0, 'VAT only when the supplier is registered')
        _o, normal = self._receipt(self.supplier, 'F-3')
        self.assertFalse(normal.self_bill_id)

    # -- statements and GRNI ------------------------------------------------------------------------------------------
    def test_supplier_statement_runs_the_balance(self):
        voucher, invoice = self._approved_voucher('S-1', qty=10000.0)
        self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 50000.0, 'account_id': self.account.id})
        statement = self.env['mos.supplier.statement'].create({'supplier_id': self.supplier.id, 'date_from': fields.Date.today() - timedelta(days=5), 'date_to': fields.Date.today()})
        statement.action_build()
        self.assertEqual(statement.opening, 0.0)
        self.assertEqual(len(statement.line_ids), 2)
        self.assertEqual(statement.closing, round(invoice.payable_amount - 50000.0, 2))
        self.assertEqual(statement.line_ids[-1].balance, statement.closing)
        with self.assertRaises(UserError):
            self.env['mos.supplier.statement'].create({'supplier_id': self.supplier.id, 'date_from': fields.Date.today(), 'date_to': fields.Date.today() - timedelta(days=1)}).action_build()

    def test_grni_lists_what_was_received_and_not_yet_invoiced(self):
        order, visit = self._receipt(self.supplier, 'G-1', qty=30000.0)
        grni = self.env['mos.grni'].create({})
        grni.action_build()
        row = grni.line_ids.filtered(lambda l: l.order_id == order)
        self.assertEqual((row.received_qty, row.invoiced_qty, row.grni_value), (30000.0, 0.0, 600000.0))
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create({'supplier_id': self.supplier.id, 'supplier_ref': 'G-INV', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': 30000.0, 'unit_price': 20.0})
        invoice.action_match()
        grni.action_build()
        row = grni.line_ids.filtered(lambda l: l.order_id == order)
        self.assertEqual((row.invoiced_qty, row.invoiced_value, row.grni_value), (30000.0, 600000.0, 0.0), 'the invoice clears it')
        early = self.env['mos.supplier.invoice'].with_user(self.apclerk).create({'supplier_id': self.supplier.id, 'supplier_ref': 'G-EARLY', 'purchase_id': self._receipt(self.supplier, 'G-2', qty=5000.0)[0].id, 'product_id': self.wheat.id, 'qty': 5000.0, 'unit_price': 20.0})
        early.action_match()
        grni.action_build()
        self.assertGreaterEqual(grni.received_total, 600000.0)

    # -- registers --------------------------------------------------------------------------------------------------------
    def test_vat_and_withholding_registers(self):
        voucher, invoice = self._approved_voucher('R-1', qty=10000.0)
        payment = self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': voucher.net_amount, 'account_id': self.account.id})
        customer = self.env['res.partner'].create({'name': 'Register customer', 'vat': 'TIN-CUST'})
        sale = self.env['mos.customer.invoice'].create({'customer_id': customer.id, 'product_id': self.wheat.id, 'qty': 1000.0, 'unit_price': 30.0})
        self.env.flush_all()
        vat_in = self.env['mos.vat.register'].search([('kind', '=', 'input'), ('document', '=', invoice.name)])
        self.assertEqual((vat_in.tin, vat_in.net_amount, vat_in.vat_amount), ('TIN-0001', 200000.0, 30000.0))
        vat_out = self.env['mos.vat.register'].search([('kind', '=', 'output'), ('document', '=', sale.name)])
        self.assertEqual((vat_out.net_amount, vat_out.vat_amount), (30000.0, 4500.0))
        wht = self.env['mos.wht.register'].search([('payment', '=', payment.name)])
        self.assertEqual((wht.tin, wht.wht_amount), ('TIN-0001', voucher.wht_amount))
        self.env['mos.payment.reversal'].with_user(self.fin).create({'payment_id': payment.id, 'reason': 'Paid the wrong account'})
        self.env.flush_all()
        self.env.invalidate_all()
        void = self.env['mos.wht.register'].search([('payment', '=', payment.name)])
        self.assertEqual((void.state, void.wht_amount), ('void', 0.0), 'a voided certificate adds nothing to the register')

    # -- journal export ------------------------------------------------------------------------------------------------------
    def _map(self, **extra):
        codes = {'inventory': '1300', 'input_vat': '1410', 'ap_control': '2100', 'wht_payable': '2250', 'bank': '1020', 'cash': '1010', 'ar_control': '1200', 'sales': '4000', 'output_vat': '2310'}
        codes.update(extra)
        for key, code in codes.items():
            if code:
                self.env['mos.account.map'].create({'key': key, 'code': code})

    def test_journal_export_is_balanced_hashed_and_once_only(self):
        voucher, invoice = self._approved_voucher('J-1', qty=10000.0)
        self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': voucher.net_amount, 'account_id': self.account.id})
        customer = self.env['res.partner'].create({'name': 'Journal customer'})
        self.env['mos.customer.invoice'].create({'customer_id': customer.id, 'product_id': self.wheat.id, 'qty': 1000.0, 'unit_price': 30.0})
        with self.assertRaises(UserError):
            self.env['mos.journal.batch'].with_user(self.fin).export(fields.Date.today(), fields.Date.today())  # nothing mapped
        self._map()
        with self.assertRaises(UserError):
            self.env['mos.journal.batch'].with_user(self.apclerk).export(fields.Date.today(), fields.Date.today())  # not finance
        batch = self.env['mos.journal.batch'].with_user(self.fin).export(fields.Date.today() - timedelta(days=1), fields.Date.today())
        self.assertRegex(batch.name, r'^JX-\d{4}-\d{5}$')
        debit = sum(batch.line_ids.mapped('debit'))
        credit = sum(batch.line_ids.mapped('credit'))
        self.assertAlmostEqual(debit, credit, places=2)
        self.assertEqual(batch.total_debit, round(debit, 2))
        content = self._raw(batch.file).decode()
        self.assertTrue(content.startswith('Date,Journal,Reference,Account,Partner,Description,Debit,Credit'))
        self.assertIn('2100', content)
        import hashlib

        self.assertEqual(batch.sha256, hashlib.sha256(content.encode()).hexdigest())
        mine = [l for l in batch.line_ids if l.reference == invoice.name]
        self.assertEqual(sum(l.debit for l in mine), invoice.net_amount + invoice.vat_amount)
        self.assertEqual(sum(l.credit for l in mine), invoice.net_amount + invoice.vat_amount)
        with self.assertRaises(UserError):
            self.env['mos.journal.batch'].with_user(self.fin).export(fields.Date.today() - timedelta(days=1), fields.Date.today())  # nothing new
        with self.assertRaises(UserError):
            batch.write({'sha256': 'x'})  # an export is final

    def test_unmapped_posting_kinds_are_named(self):
        self._approved_voucher('J-2', qty=10000.0)
        self._map(wht_payable=None)
        with self.assertRaises(UserError) as caught:
            self.env['mos.journal.batch'].with_user(self.fin).export(fields.Date.today() - timedelta(days=1), fields.Date.today())
        self.assertIn('wht_payable', str(caught.exception))

    # -- payment runs ------------------------------------------------------------------------------------------------------
    def test_payment_run_pays_what_is_due_and_stops_whole_on_a_rule(self):
        v1, _i1 = self._approved_voucher('P-1', qty=10000.0)
        v2, _i2 = self._approved_voucher('P-2', qty=12000.0)
        run = self.env['mos.payment.run'].with_user(self.cashier).create({'account_id': self.account.id, 'method': 'transfer'})
        run.action_propose()
        self.assertTrue({v1, v2} <= set(run.line_ids.voucher_id))
        for line in run.line_ids.filtered(lambda l: l.voucher_id not in (v1, v2)):
            line.include = False
        self.assertEqual(run.total, v1.remaining_amount + v2.remaining_amount)
        run.line_ids.filtered(lambda l: l.voucher_id == v2).include = False
        run.with_user(self.cashier).action_execute()
        self.assertEqual(run.state, 'executed')
        self.assertEqual(v1.state, 'paid')
        self.assertEqual(v2.state, 'approved')
        self.assertTrue(run.line_ids.filtered('include').payment_id)
        self.assertEqual(run.line_ids.filtered('include').payment_id.reference, run.name)
        # the clerk who raised vouchers cannot be the one who pays them: the whole run stops
        both = new_test_user(self.env, login='fpboth', groups='mos_payables.group_mos_ap_clerk,mos_payables.group_mos_cashier')
        both.mos_site_ids = [Command.set(self.site.ids)]
        v3, _i3 = self._approved_voucher('P-3', qty=9000.0, clerk=both)
        bad = self.env['mos.payment.run'].with_user(both).create({'account_id': self.account.id})
        bad.action_propose()
        for line in bad.line_ids.filtered(lambda l: l.voucher_id != v3):
            line.include = False
        with self.assertRaises(UserError):
            bad.with_user(both).action_execute()
        self.assertEqual(v3.state, 'approved')
        with self.assertRaises(UserError):
            run.action_execute()  # already executed
        with self.assertRaises(UserError):
            self.env['mos.payment.run'].create({'account_id': self.account.id, 'method': 'cheque'}).action_execute()
