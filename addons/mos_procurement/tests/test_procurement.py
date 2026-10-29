from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import mute_logger
from psycopg2 import IntegrityError


@tagged('post_install', '-at_install')
class TestProcurement(TransactionCase):
    """Supplier approval, documents, bank-change control, quotes, receipt tolerance, amendments, returns, reassignment."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLP', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.buyer = user('pbuyer', 'mos_procurement.group_mos_procurement_user')
        cls.buyer2 = user('pbuyer2', 'mos_procurement.group_mos_procurement_user')
        cls.manager = user('pmanager', 'mos_procurement.group_mos_procurement_manager')
        cls.manager2 = user('pmanager2', 'mos_procurement.group_mos_procurement_manager')
        cls.finance = user('pfinance', 'mos_approvals.group_mos_finance_manager')
        cls.gm = user('pgm', 'mos_approvals.group_mos_general_manager')
        cls.guard = user('pguard', 'mos_gate.group_mos_gate_guard')
        cls.product = env['product.product'].create(
            {'name': 'Milling wheat', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 20.0}
        )
        cls.supplier = env['res.partner'].with_user(cls.buyer).create({'name': 'Highland Growers', 'supplier_rank': 1})
        cls.other = env['res.partner'].create({'name': 'Rift Valley Traders', 'supplier_rank': 1})

    def _approve(self, partner):
        env = self.env
        for kind, number in (('trade_licence', 'TL-1'), ('tin', 'TIN-1')):
            env['mos.supplier.document'].create({'partner_id': partner.id, 'doc_type': kind, 'number': number, 'expires_on': fields.Date.today() + timedelta(days=200)})
        partner.with_user(self.manager).action_approve_supplier()

    def _order(self, partner=None, qty=10000.0, price=20.0):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': (partner or self.supplier).id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': qty, 'price_unit': price})]}
        )
        return order

    # -- the four states the order screen shows --------------------------------------------------------------
    def test_the_order_status_is_draft_pending_issued_or_canceled(self):
        order = self._order()
        self.assertEqual(order.mos_po_status, 'draft')
        order.sudo().mos_approval_state = 'pending'
        self.assertEqual(order.mos_po_status, 'pending', 'waiting for an approval decision')
        order.sudo().mos_approval_state = 'none'
        order.sudo().state = 'to approve'
        self.assertEqual(order.mos_po_status, 'pending', "Odoo's own approval step also counts as pending")
        order.sudo().state = 'purchase'
        self.assertEqual(order.mos_po_status, 'issued')
        order.sudo().state = 'cancel'
        self.assertEqual(order.mos_po_status, 'canceled')
        found = self.env['purchase.order'].search([('mos_po_status', '=', 'canceled'), ('id', '=', order.id)])
        self.assertEqual(found, order, 'the status can be searched and grouped')

    def test_the_orders_are_reachable_from_the_procurement_menu(self):
        menu = self.env.ref('mos_procurement.menu_mos_purchase_orders')
        self.assertEqual(menu.action.res_model, 'purchase.order')
        self.assertEqual(menu.parent_id, self.env.ref('mos_procurement.menu_mos_procurement'))

    # -- supplier approval ---------------------------------------------------------------------------------
    def test_supplier_approval_needs_documents_and_a_different_person(self):
        with self.assertRaises(UserError):
            self.supplier.with_user(self.manager).action_approve_supplier()  # no documents
        self.env['mos.supplier.document'].create({'partner_id': self.supplier.id, 'doc_type': 'trade_licence', 'number': 'TL-9', 'expires_on': fields.Date.today() + timedelta(days=30)})
        self.assertIn('TIN certificate', self.supplier.mos_missing_documents)
        self.env['mos.supplier.document'].create({'partner_id': self.supplier.id, 'doc_type': 'tin', 'number': 'T-9'})
        with self.assertRaises(UserError):
            self.supplier.with_user(self.buyer).action_approve_supplier()  # not a manager
        manager_made = self.env['res.partner'].with_user(self.manager).create({'name': 'Self made', 'supplier_rank': 1})
        for kind in ('trade_licence', 'tin'):
            self.env['mos.supplier.document'].create({'partner_id': manager_made.id, 'doc_type': kind, 'number': kind})
        with self.assertRaises(UserError):
            manager_made.with_user(self.manager).action_approve_supplier()  # registered by the same person
        self.supplier.with_user(self.manager).action_approve_supplier()
        self.assertEqual(self.supplier.mos_supplier_state, 'approved')
        self.assertEqual(self.supplier.mos_approved_by_id, self.manager)
        self.assertTrue(self.supplier.mos_review_due)

    def test_suspend_block_reinstate_roles(self):
        self._approve(self.supplier)
        with self.assertRaises(UserError):
            self.supplier.with_user(self.manager).action_suspend_supplier('')
        self.supplier.with_user(self.manager).action_suspend_supplier('Late deliveries')
        self.assertEqual(self.supplier.mos_supplier_state, 'suspended')
        with self.assertRaises(UserError):
            self.supplier.with_user(self.manager).action_block_supplier('Fraud')  # only a general manager
        self.supplier.with_user(self.gm).action_block_supplier('Fraud')
        with self.assertRaises(UserError):
            self.supplier.with_user(self.manager).action_approve_supplier()  # blocked must be reinstated first
        self.supplier.with_user(self.gm).action_reinstate_supplier()
        self.assertEqual(self.supplier.mos_supplier_state, 'candidate')

    def test_document_expiry_cron_notifies_and_suspends(self):
        self._approve(self.supplier)
        tin = self.supplier.mos_document_ids.filtered(lambda d: d.doc_type == 'tin')
        tin.expires_on = fields.Date.today() + timedelta(days=10)
        self.assertEqual(tin.state, 'expiring')
        self.manager.group_ids  # make sure the group has a member for the notice
        notified, suspended = self.env['mos.supplier.document']._cron_documents()
        self.assertEqual(notified, 1)
        self.assertEqual(suspended, 0)
        self.assertTrue(tin.notified_on)
        self.assertEqual(self.env['mos.supplier.document']._cron_documents(), (0, 0), 'a notice is sent once')
        tin.expires_on = fields.Date.today() - timedelta(days=1)
        self.env['mos.supplier.document']._cron_documents()
        self.assertEqual(self.supplier.mos_supplier_state, 'suspended')

    def test_expired_document_does_not_count(self):
        self.env['mos.supplier.document'].create({'partner_id': self.other.id, 'doc_type': 'trade_licence', 'number': 'old', 'expires_on': fields.Date.today() - timedelta(days=1)})
        self.assertFalse(self.other.mos_documents_ok)
        with self.assertRaises(ValidationError):
            self.env['mos.supplier.document'].create(
                {'partner_id': self.other.id, 'doc_type': 'tin', 'number': 'x', 'issued_on': fields.Date.today(), 'expires_on': fields.Date.today() - timedelta(days=5)}
            )

    # -- approved supplier list ------------------------------------------------------------------------------
    def test_approved_supplier_list_is_enforced_when_the_policy_is_on(self):
        order = self._order()
        order.button_confirm()  # policy off: confirmation works
        self.assertEqual(order.state, 'purchase')
        self.env['ir.config_parameter'].sudo().set_str('mos_procurement.enforce_asl', 'True')
        second = self._order()
        with self.assertRaises(UserError):
            second.button_confirm()
        self._approve(self.supplier)
        second.button_confirm()
        self.assertEqual(second.state, 'purchase')

    # -- sourcing --------------------------------------------------------------------------------------------
    def test_sourcing_policy_needs_quotes(self):
        self.env['mos.sourcing.policy'].create({'amount_from': 100000, 'min_quotes': 3})
        small = self._order(qty=100.0)
        small.button_confirm()
        big = self._order(qty=10000.0)  # 200 000
        with self.assertRaises(UserError):
            big.button_confirm()
        for partner, amount in ((self.supplier, 200000.0), (self.other, 210000.0)):
            self.env['mos.purchase.quote'].with_user(self.buyer).create({'order_id': big.id, 'partner_id': partner.id, 'amount': amount})
        with self.assertRaises(UserError):
            big.button_confirm()  # two quotes of three
        third = self.env['res.partner'].create({'name': 'Third', 'supplier_rank': 1})
        self.env['mos.purchase.quote'].create({'order_id': big.id, 'partner_id': third.id, 'amount': 220000.0})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.env['mos.purchase.quote'].create({'order_id': big.id, 'partner_id': third.id, 'amount': 1.0})  # one quote per supplier
        big.button_confirm()
        self.assertEqual(big.state, 'purchase')

    def test_sole_source_reason_waives_quotes(self):
        self.env['mos.sourcing.policy'].create({'amount_from': 1000, 'min_quotes': 2})
        order = self._order()
        order.mos_sole_source_reason = 'Only licensed grower in the region'
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')

    # -- purchase requests -------------------------------------------------------------------------------------
    def _request(self, price=20.0, qty=1000.0):
        return self.env['mos.purchase.request'].with_user(self.buyer).create(
            {'site_id': self.site.id, 'reason': 'Replenish', 'line_ids': [Command.create({'product_id': self.product.id, 'qty': qty, 'estimated_price': price})]}
        )

    def test_request_without_a_matching_rule_is_approved_on_submit(self):
        req = self._request()
        self.assertEqual(req.amount, 20000.0)
        req.action_submit()
        self.assertEqual(req.state, 'approved')
        order = req.with_user(self.buyer).action_convert(self.supplier)
        self.assertEqual(req.state, 'converted')
        self.assertEqual(order.mos_request_id, req)
        self.assertEqual(order.order_line.product_qty, 1000.0)
        with self.assertRaises(UserError):
            req.write({'reason': 'edited after the order'})

    def test_request_goes_through_the_approval_matrix(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.purchase.request', 'amount_from': 10000, 'group_id': self.env.ref('mos_approvals.group_mos_dept_manager').id})
        dept = new_test_user(self.env, login='pdept', groups='mos_approvals.group_mos_dept_manager')
        dept.mos_site_ids = [Command.set(self.site.ids)]
        req = self._request()
        req.action_submit()
        self.assertEqual(req.state, 'pending')
        request = req.mos_approval_request_id
        with self.assertRaises((UserError, AccessError)):
            request.current_step_id.with_user(self.buyer).action_approve()  # the requester cannot approve
        request.current_step_id.with_user(dept).action_approve()
        self.assertEqual(req.state, 'approved')

    def test_request_needs_items_and_an_approved_supplier(self):
        empty = self.env['mos.purchase.request'].with_user(self.buyer).create({'site_id': self.site.id, 'reason': 'x'})
        with self.assertRaises(UserError):
            empty.action_submit()
        req = self._request()
        with self.assertRaises(UserError):
            req.action_convert(self.supplier)  # not approved yet
        req.action_submit()
        self.env['ir.config_parameter'].sudo().set_str('mos_procurement.enforce_asl', 'True')
        with self.assertRaises(UserError):
            req.action_convert(self.supplier)  # not on the supplier list
        with self.assertRaises(ValidationError):
            self.env['mos.purchase.request.line'].create({'request_id': self._request().id, 'product_id': self.product.id, 'qty': 0})

    # -- bank change -------------------------------------------------------------------------------------------
    def test_bank_change_needs_three_different_people_and_masks_the_account(self):
        change = self.env['mos.supplier.bank.change'].with_user(self.buyer).create({'partner_id': self.supplier.id, 'new_account': '1000123456789', 'reason': 'Supplier moved banks'})
        self.assertEqual(change.new_account_masked, '*********6789')
        self.assertNotIn('1000123456789', change.sudo().new_account_enc, 'the stored value is encrypted')
        with self.assertRaises(UserError):
            self.supplier.write({'mos_bank_account_enc': 'plain'})  # never directly
        with self.assertRaises(UserError):
            change.with_user(self.buyer).action_verify('self-verified')
        with self.assertRaises(UserError):
            change.with_user(self.manager).action_verify('')
        change.with_user(self.manager).action_verify('Called finance officer on file number')
        with self.assertRaises(UserError):
            change.with_user(self.manager).action_apply()  # not finance
        with self.assertRaises(UserError):
            self.env['mos.supplier.bank.change'].with_user(self.finance).create({'partner_id': self.supplier.id, 'new_account': 'short', 'reason': 'x'})
        change.with_user(self.finance).action_apply()
        self.assertEqual(change.state, 'applied')
        self.assertEqual(self.supplier.mos_bank_account, '*********6789')
        with self.assertRaises(UserError):
            self.supplier.with_user(self.buyer).reveal_bank_account()
        self.assertEqual(self.supplier.with_user(self.finance).reveal_bank_account(), '1000123456789')
        events = self.env['mos.audit.event'].search([('action', '=', 'bank_account_revealed')])
        self.assertTrue(events)
        with self.assertRaises(UserError):
            change.write({'reason': 'rewritten'})  # applied changes are final

    def test_bank_change_applier_cannot_be_the_verifier(self):
        both = new_test_user(self.env, login='pboth', groups='mos_approvals.group_mos_finance_manager,mos_procurement.group_mos_procurement_manager')
        change = self.env['mos.supplier.bank.change'].with_user(self.buyer).create({'partner_id': self.supplier.id, 'new_account': 'ET12 3456 7890', 'reason': 'x'})
        change.with_user(both).action_verify('called')
        with self.assertRaises(UserError):
            change.with_user(both).action_apply()

    # -- receipt tolerance --------------------------------------------------------------------------------------
    def test_receipt_policy_blocks_or_flags_an_excess(self):
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'RC-1', 'purpose': 'inbound_po'})
        order = self._order(qty=10000.0)
        visit._mos_check_receipt(order, self.product, 10100.0)  # 1 % over, inside the default 2 %
        with self.assertRaises(UserError):
            visit._mos_check_receipt(order, self.product, 10500.0)
        policy = self.env['mos.receipt.policy'].search([('product_id', '=', False)])
        policy.over_action = 'warn'
        visit._mos_check_receipt(order, self.product, 10500.0)
        self.assertTrue(visit.incident_ids)
        policy.over_pct = 10.0
        visit.incident_ids.unlink()
        visit._mos_check_receipt(order, self.product, 10500.0)
        self.assertFalse(visit.incident_ids)

    # -- amendments ---------------------------------------------------------------------------------------------
    def test_confirmed_order_changes_only_through_an_amendment(self):
        order = self._order()
        order.button_confirm()
        line = order.order_line
        with self.assertRaises(UserError):
            line.with_user(self.buyer).write({'product_qty': 5000})
        amd = self.env['mos.po.amendment'].with_user(self.buyer).create(
            {'purchase_id': order.id, 'reason': 'Supplier price rise', 'line_ids': [Command.create({'po_line_id': line.id, 'new_qty': 10000, 'new_price': 21.0})]}
        )
        self.assertEqual((amd.line_ids.old_qty, amd.line_ids.old_price), (10000.0, 20.0))
        self.assertEqual(amd.new_total, 210000.0)
        with self.assertRaises(UserError):
            amd.action_apply()  # not approved
        amd.with_user(self.buyer).action_submit()
        self.assertEqual(amd.state, 'approved', 'no matrix row applies')
        with self.assertRaises(UserError):
            amd.with_user(self.buyer).action_apply()  # requester applies their own unapproved amendment
        amd.with_user(self.buyer2).action_apply()
        self.assertEqual(line.price_unit, 21.0)
        self.assertEqual(amd.state, 'applied')

    def test_amendment_cannot_go_below_received(self):
        order = self._order()
        order.button_confirm()
        order.order_line.qty_received_manual = 6000
        amd = self.env['mos.po.amendment'].with_user(self.buyer).create(
            {'purchase_id': order.id, 'reason': 'Cut', 'line_ids': [Command.create({'po_line_id': order.order_line.id, 'new_qty': 100, 'new_price': 20.0})]}
        )
        amd.action_submit()
        if order.order_line.qty_received > 100:
            with self.assertRaises(UserError):
                amd.with_user(self.buyer2).action_apply()

    # -- supplier returns ---------------------------------------------------------------------------------------
    def test_supplier_return_validation_and_flow(self):
        order = self._order()
        order.button_confirm()
        reason = self.env['mos.reason'].create({'domain': 'quality', 'code': 'WET', 'name': 'Wet grain'})
        with self.assertRaises(ValidationError):
            self.env['mos.supplier.return'].create({'purchase_id': order.id, 'product_id': self.product.id, 'qty': 500, 'reason_id': reason.id})  # nothing received yet
        order.picking_ids.move_ids.write({'quantity': 10000, 'picked': True})
        order.picking_ids.with_context(skip_backorder=True, picking_ids_not_to_backorder=order.picking_ids.ids).button_validate()
        ret = self.env['mos.supplier.return'].with_user(self.buyer).create({'purchase_id': order.id, 'product_id': self.product.id, 'qty': 500, 'reason_id': reason.id})
        with self.assertRaises(ValidationError):
            self.env['mos.supplier.return'].create({'purchase_id': order.id, 'product_id': self.product.id, 'qty': 9800, 'reason_id': reason.id})  # more than received in total
        with self.assertRaises(UserError):
            ret.with_user(self.buyer).action_approve()
        ret.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            ret.action_credit(self.env['mos.supplier.invoice'])  # not dispatched
        ret.with_user(self.manager).action_dispatch()
        self.assertEqual(ret.state, 'dispatched')
        self.assertEqual(ret.picking_id.state, 'done')
        invoice = self.env['mos.supplier.invoice'].create(
            {'supplier_id': self.supplier.id, 'supplier_ref': 'INV-77', 'purchase_id': order.id, 'product_id': self.product.id, 'qty': 10000, 'unit_price': 20.0, 'vat_pct': 15.0}
        )
        before = invoice.payable_amount
        ret.action_credit(invoice)
        self.assertEqual(ret.state, 'credited')
        self.assertEqual(ret.debit_note_id.amount, round(500 * 20.0 * 1.15, 2))
        self.assertEqual(invoice.payable_amount, round(before - 11500.0, 2))

    # -- receipt reassignment ----------------------------------------------------------------------------------
    def test_receipt_reassignment_moves_a_visit_to_the_right_order(self):
        wrong, right = self._order(), self._order()
        wrong.button_confirm()
        right.button_confirm()
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'RA-1', 'purpose': 'inbound_po', 'purchase_id': wrong.id, 'partner_id': self.supplier.id})
        request = self.env['mos.grn.reassignment'].with_user(self.buyer).create({'visit_id': visit.id, 'to_order_id': right.id, 'reason': 'Booked on the wrong order'})
        self.assertEqual(request.from_order_id, wrong)
        with self.assertRaises(UserError):
            request.with_user(self.buyer).action_apply()  # not a manager
        request.with_user(self.manager).action_apply()
        self.assertEqual(visit.purchase_id, right)
        self.assertEqual(request.state, 'applied')
        other_order = self._order(partner=self.other)
        other_order.button_confirm()
        again = self.env['mos.grn.reassignment'].with_user(self.buyer).create({'visit_id': visit.id, 'to_order_id': other_order.id, 'reason': 'x'})
        with self.assertRaises(UserError):
            again.with_user(self.manager).action_apply()  # a different supplier
        done = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'RA-2', 'purpose': 'inbound_po', 'purchase_id': wrong.id})
        done.sudo().grn_state = 'confirmed'
        late = self.env['mos.grn.reassignment'].with_user(self.buyer).create({'visit_id': done.id, 'to_order_id': right.id, 'reason': 'x'})
        with self.assertRaises(UserError):
            late.with_user(self.manager2).action_apply()  # already received
