import base64

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


def pdf(tag):
    return base64.b64encode(b'%PDF-1.4\n% ' + tag.encode() + b'\n%%EOF\n').decode()


@tagged('post_install', '-at_install')
class TestDocuments(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.clerk = new_test_user(env, login='doc_clerk', groups='mos_payables.group_mos_ap_clerk')
        cls.checker = new_test_user(env, login='doc_checker', groups='mos_payables.group_mos_ap_clerk')
        cls.cashier = new_test_user(env, login='doc_cashier', groups='mos_payables.group_mos_cashier')
        cls.fin = new_test_user(env, login='doc_fin', groups='mos_approvals.group_mos_finance_manager')
        cls.supplier = env['res.partner'].create({'name': 'Doc Union', 'supplier_rank': 1, 'mos_vat_pct': 15.0, 'mos_wht_pct': 2.0})
        cls.product = env['product.product'].create({'name': 'Doc durum', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 10})
        cls.account = env['mos.paying.account'].create({'name': 'Doc bank', 'kind': 'bank'})
        cls.icp = env['ir.config_parameter'].sudo()

    def _invoice(self, qty=1000, price=10.0, ref='DOC-1'):
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': qty, 'price_unit': price})]})
        po.button_confirm()
        picking = po.picking_ids
        picking.move_ids.write({'quantity': qty, 'picked': True})
        picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        return self.env['mos.supplier.invoice'].with_user(self.clerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': ref, 'purchase_id': po.id, 'product_id': self.product.id, 'qty': qty, 'unit_price': price}
        )

    def _attach(self, record, kind, tag, user=None):
        return self.env['mos.attachment'].with_user(user or self.clerk).create(
            {'name': f'{tag}.pdf', 'file': pdf(tag), 'res_model': record._name, 'res_id': record.id, 'kind': kind}
        )

    # -- typed slots and verification -------------------------------------------------------------------------------
    def test_files_sit_on_the_document_by_kind(self):
        inv = self._invoice()
        self._attach(inv, 'supplier_invoice', 'a')
        self._attach(inv, 'tax_document', 'b')
        inv.invalidate_recordset()
        self.assertEqual(inv.mos_attachment_count, 2)
        self.assertEqual(inv.mos_unverified_count, 2)
        self.assertEqual(set(inv.mos_attachment_ids.mapped('kind')), {'supplier_invoice', 'tax_document'})

    def test_a_second_person_verifies_and_the_uploader_cannot(self):
        inv = self._invoice()
        att = self._attach(inv, 'supplier_invoice', 'v')
        with self.assertRaises(UserError):
            att.with_user(self.clerk).action_verify()
        att.with_user(self.checker).action_verify()
        self.assertEqual((att.verify_state, att.verified_by_id), ('verified', self.checker))
        inv.invalidate_recordset()
        self.assertEqual(inv.mos_unverified_count, 0)
        self.assertTrue(inv._mos_has_verified('supplier_invoice'))
        self.assertFalse(inv._mos_has_verified('tax_document'))

    def test_a_verified_file_never_changes(self):
        inv = self._invoice()
        att = self._attach(inv, 'supplier_invoice', 'w')
        att.with_user(self.checker).action_verify()
        with self.assertRaises(UserError):
            att.sudo().write({'file': pdf('other')})

    def test_verification_is_on_the_audit_trail(self):
        inv = self._invoice()
        att = self._attach(inv, 'supplier_invoice', 'x')
        att.with_user(self.checker).action_verify()
        self.assertTrue(self.env['mos.audit.event'].sudo().search([('action', '=', 'document_verified')], limit=1))

    # -- the optional rules ----------------------------------------------------------------------------------------
    def test_the_invoice_document_rule_is_off_by_default_and_works_when_on(self):
        inv = self._invoice(ref='DOC-2')
        inv.action_match()
        self.assertEqual(inv.state, 'payable', 'no rule, no document needed')
        self.icp.set_str('mos_documents.require_verified_invoice', 'True')
        other = self._invoice(ref='DOC-3')
        with self.assertRaises(UserError):
            other.action_match()
        att = self._attach(other, 'supplier_invoice', 'y')
        with self.assertRaises(UserError):
            other.action_match()  # attached but not verified
        att.with_user(self.checker).action_verify()
        other.action_match()
        self.assertEqual(other.state, 'payable')

    def test_a_deposit_slip_is_needed_for_a_transfer_when_the_rule_is_on(self):
        inv = self._invoice(ref='DOC-4')
        inv.action_match()
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': inv.id})]})
        voucher.action_submit()
        self.icp.set_str('mos_documents.require_deposit_slip', 'True')
        pay = {'voucher_id': voucher.id, 'amount': 1000, 'account_id': self.account.id}
        with self.assertRaises(UserError):
            self.env['mos.payment'].with_user(self.cashier).create(pay)
        self._attach(voucher, 'deposit_slip', 'slip')
        self.env['mos.payment'].with_user(self.cashier).create(pay)
        self.assertEqual(voucher.state, 'partially_paid')

    # -- optional approval of every invoice (FR-AP-020) ----------------------------------------------------------------
    def test_a_matched_invoice_is_payable_at_once_unless_every_invoice_needs_approval(self):
        inv = self._invoice(ref='DOC-5')
        inv.action_match()
        self.assertEqual((inv.match_state, inv.state), ('matched', 'payable'))
        self.assertFalse(inv.approval_gate)

    def test_with_the_option_on_a_matched_invoice_waits_for_its_approval(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.supplier.invoice', 'amount_from': 0, 'group_id': self.env.ref('mos_approvals.group_mos_finance_manager').id})
        self.icp.set_str('mos_payables.approve_every_invoice', 'True')
        inv = self._invoice(ref='DOC-6')
        inv.action_match()
        self.assertEqual((inv.match_state, inv.state), ('matched', 'draft'), 'matched, but not yet payable')
        self.assertTrue(inv.approval_gate, 'the form offers "Send for approval"')
        inv.action_mos_submit_approval()
        self.assertEqual(inv.mos_approval_state, 'pending')
        self.assertFalse(inv.approval_gate)
        self.assertEqual(inv.state, 'draft')
        self.assertEqual(inv.mos_approval_request_id.amount, inv.gross_amount, 'approved as a whole, by its gross amount')
        inv.mos_approval_request_id.step_ids.filtered(lambda s: s.state == 'pending').with_user(self.fin).action_approve('fine')
        self.assertEqual((inv.mos_approval_state, inv.state), ('approved', 'payable'))

    def test_a_rejected_invoice_stays_unpayable_and_can_be_sent_again(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.supplier.invoice', 'amount_from': 0, 'group_id': self.env.ref('mos_approvals.group_mos_finance_manager').id})
        self.icp.set_str('mos_payables.approve_every_invoice', 'True')
        inv = self._invoice(ref='DOC-7')
        inv.action_match()
        inv.action_mos_submit_approval()
        inv.mos_approval_request_id.step_ids.filtered(lambda s: s.state == 'pending').with_user(self.fin).action_reject('wrong supplier')
        self.assertEqual((inv.mos_approval_state, inv.state), ('rejected', 'draft'))
        self.assertTrue(inv.approval_gate)

    # -- the "I agree" statement reaches every module's own Submit button -------------------------------------------
    def test_a_modules_own_submit_opens_the_statement_dialog_and_finishes_after_agreeing(self):
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        site = self.env['mos.site'].create({'name': 'Doc mill', 'code': 'DOCM', 'warehouse_id': warehouse.id})
        buyer = new_test_user(self.env, login='doc_buyer', groups='mos_procurement.group_mos_procurement_user')
        buyer.mos_site_ids = [Command.set(site.ids)]
        new_test_user(self.env, login='doc_dept', groups='mos_approvals.group_mos_dept_manager')
        self.env['mos.approval.rule'].create({'doc_type': 'mos.purchase.request', 'amount_from': 0, 'group_id': self.env.ref('mos_approvals.group_mos_dept_manager').id})
        self.icp.set_str('mos_approvals.require_attestation', 'True')
        request = self.env['mos.purchase.request'].with_user(buyer).create(
            {'site_id': site.id, 'reason': 'Bags', 'line_ids': [Command.create({'product_id': self.product.id, 'qty': 10.0, 'estimated_price': 5.0})]}
        )
        dialog = request.action_submit()
        self.assertEqual(dialog['res_model'], 'mos.approval.submit')
        self.assertEqual(dialog['context']['default_method'], 'action_submit')
        self.assertEqual((request.state, request.mos_approval_state), ('draft', 'none'), 'nothing happened before the statement was confirmed')
        wizard = self.env['mos.approval.submit'].with_user(buyer).with_context(**dialog['context']).create({})
        wizard.agree = True
        wizard.action_confirm()
        self.assertEqual((request.state, request.mos_approval_state), ('pending', 'pending'))
        self.assertTrue(request.mos_approval_request_id.submit_attestation)

    def test_a_voucher_is_not_half_submitted_while_the_statement_is_missing(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.payment.voucher', 'amount_from': 0, 'group_id': self.env.ref('mos_approvals.group_mos_finance_manager').id})
        inv = self._invoice(ref='DOC-8')
        inv.action_match()
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': inv.id})]})
        self.icp.set_str('mos_approvals.require_attestation', 'True')
        dialog = voucher.with_user(self.clerk).action_submit()
        self.assertEqual(dialog['res_model'], 'mos.approval.submit')
        self.assertEqual((voucher.state, inv.state), ('draft', 'payable'), 'neither the voucher nor its invoices moved')
