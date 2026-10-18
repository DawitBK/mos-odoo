from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestFlows(TransactionCase):
    """Flow A with automatic receipts, flow B into quarantine, flow C from the lab, payables from receipts, pre-flight, exceptions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLF', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('fguard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('fverifier', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('fanalyst', 'mos_lab.group_mos_lab_analyst')
        cls.manager = user('fmanager', 'mos_lab.group_mos_lab_manager')
        cls.store = user('fstore', 'mos_inbound.group_mos_storekeeper')
        cls.buyer = user('fbuyer', 'purchase.group_purchase_user,mos_core.group_mos_user')
        cls.apclerk = user('fap', 'mos_payables.group_mos_ap_clerk')
        cls.product = env['product.product'].create(
            {'name': 'Packed flour / wheat', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 20.0}
        )
        cls.supplier = env['res.partner'].create({'name': 'Flow Supplier', 'supplier_rank': 1})
        spec = env['mos.lab.spec'].create({'product_id': cls.product.id, 'line_ids': [Command.create({'name': 'Moisture %', 'min_value': 0, 'max_value': 13.5})]})
        spec.action_approve()
        cls.spec = spec
        cls.stock = warehouse.lot_stock_id

    def _order(self, qty=40000.0, partner=None):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': (partner or self.supplier).id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': qty, 'price_unit': 20.0})]}
        )
        order.with_user(self.buyer).button_confirm()
        return order

    def _qty(self, location):
        return sum(self.env['stock.quant'].search([('product_id', '=', self.product.id), ('location_id', '=', location.id)]).mapped('quantity'))

    def _results(self, inspection, moisture=12.0):
        inspection = inspection.with_user(self.analyst)
        inspection.record_result(self.spec.line_ids, moisture)
        inspection.action_submit_results()
        return inspection

    def _flow_a_visit(self, order, plate='FA-1'):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': order.partner_id.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(50000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(50000.0, 15000.0)
        return visit

    # -- flow A with automatic receipts and payables --------------------------------------------------------
    def test_automatic_receipt_posts_on_preparation_and_starts_the_payment(self):
        self.env['mos.flow.setting']._singleton().grn_mode = 'automatic'
        order = self._order()
        visit = self._flow_a_visit(order)
        inspection = visit.with_user(self.analyst).action_open_inspection()
        self._results(inspection)
        inspection.with_user(self.manager).decide('accept')
        visit = visit.with_user(self.guard)
        visit.action_prepare_grn()
        self.assertEqual(visit.grn_state, 'confirmed', 'automatic mode posts without the storekeeper')
        self.assertEqual(self._qty(self.stock), 35000.0)
        draft = visit.pv_draft_id
        self.assertTrue(draft)
        self.assertEqual(draft.state, 'awaiting_invoice')
        self.assertEqual(draft.received_qty, 35000.0)
        self.assertEqual(draft.expected_gross, 700000.0)
        self.assertEqual(self.env['mos.pv.draft'].for_visit(visit), draft, 'a repeat never makes a second payment draft')
        self.assertEqual(self.env['mos.pv.draft'].search_count([('visit_id', '=', visit.id)]), 1)
        self.assertTrue(self.env['mos.exception'].search([('kind', '=', 'pv_setup'), ('res_id', '=', draft.id)]), 'no payment account on file raises a task, not a block')

    def test_invoice_links_to_a_voucher_draft_and_auto_submit(self):
        order = self._order()
        visit = self._flow_a_visit(order, 'FA-2')
        inspection = visit.with_user(self.analyst).action_open_inspection()
        self._results(inspection)
        inspection.with_user(self.manager).decide('accept')
        visit = visit.with_user(self.guard)
        visit.action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        draft = visit.pv_draft_id
        self.assertEqual(draft.state, 'awaiting_invoice')
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': 'F-100', 'purchase_id': order.id, 'product_id': self.product.id, 'qty': 35000.0, 'unit_price': 20.0}
        )
        invoice.action_match()
        self.assertEqual(invoice.state, 'payable')
        self.assertEqual(draft.state, 'drafted')
        self.assertEqual(draft.voucher_id.line_ids.invoice_id, invoice)
        self.assertEqual(draft.voucher_id.state, 'draft')

        self.env['mos.flow.setting']._singleton().pv_mode = 'auto_submit'
        order2 = self._order()
        visit2 = self._flow_a_visit(order2, 'FA-3')
        insp2 = visit2.with_user(self.analyst).action_open_inspection()
        self._results(insp2)
        insp2.with_user(self.manager).decide('accept')
        visit2.with_user(self.guard).action_prepare_grn()
        visit2.with_user(self.store).action_confirm_grn()
        invoice2 = self.env['mos.supplier.invoice'].with_user(self.apclerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': 'F-101', 'purchase_id': order2.id, 'product_id': self.product.id, 'qty': 35000.0, 'unit_price': 20.0}
        )
        invoice2.action_match()
        self.assertEqual(visit2.pv_draft_id.state, 'submitted')
        self.assertIn(visit2.pv_draft_id.voucher_id.state, ('submitted', 'approved'))

    def test_rejected_load_starts_no_payment(self):
        order = self._order()
        visit = self._flow_a_visit(order, 'FA-4')
        inspection = visit.with_user(self.analyst).action_open_inspection()
        self._results(inspection, moisture=16.0)
        inspection.with_user(self.manager).decide('reject', note='Wet')
        self.assertFalse(self.env['mos.pv.draft'].for_visit(visit))

    # -- pre-flight and receipt exceptions -----------------------------------------------------------------
    def test_preflight_stops_a_receipt_for_a_blocked_supplier(self):
        order = self._order()
        visit = self._flow_a_visit(order, 'FA-5')
        inspection = visit.with_user(self.analyst).action_open_inspection()
        self._results(inspection)
        inspection.with_user(self.manager).decide('accept')
        order.partner_id.sudo().mos_supplier_state = 'blocked'
        self.assertIn('the supplier is blocked', visit._mos_preflight(self.product, 100.0))
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_prepare_grn()
        with self.assertRaises(UserError):
            visit.action_check_preflight()
        order.partner_id.sudo().mos_supplier_state = 'candidate'
        self.assertTrue(visit.action_check_preflight())

    def test_failed_receipt_becomes_an_exception_and_needs_an_override_to_leave(self):
        order = self._order()
        visit = self._flow_a_visit(order, 'FA-6')
        inspection = visit.with_user(self.analyst).action_open_inspection()
        self._results(inspection)
        inspection.with_user(self.manager).decide('accept')
        order.partner_id.sudo().mos_supplier_state = 'blocked'
        self.assertFalse(visit.with_user(self.guard).action_prepare_grn_safely())
        self.assertIn('blocked', visit.receipt_exception_note)
        task = self.env['mos.exception'].search([('kind', '=', 'receipt_exception'), ('res_id', '=', visit.id)])
        self.assertEqual(task.severity, 'high')
        self.assertTrue(any('receipt failed' in p for p in visit._exit_blockers()))
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_supervisor_override('now')  # a guard is not a supervisor
        visit.with_user(self.store).action_supervisor_override('Supplier unblocked in the morning')
        self.assertFalse(any('receipt failed' in p for p in visit._exit_blockers()))

    # -- flow B ----------------------------------------------------------------------------------------------
    def test_flow_b_receives_into_quarantine_then_releases_on_the_decision(self):
        order = self._order(qty=5000.0)
        visit = self.env['mos.gate.visit'].with_user(self.guard).create(
            {'site_id': self.site.id, 'plate': 'FB-1', 'purchase_id': order.id, 'partner_id': self.supplier.id, 'flow': 'B', 'bags': 100}
        )
        inspection = visit.with_user(self.store).action_receive_quarantine(4900.0)
        quarantine = self.env['mos.stock.status.change'].status_location(self.site, 'quarantine')
        self.assertEqual(self._qty(quarantine), 4900.0)
        self.assertEqual(self._qty(self.stock), 0.0)
        self.assertEqual(visit.grn_state, 'confirmed')
        self.assertEqual(inspection.visit_id, visit)
        with self.assertRaises(UserError):
            visit.with_user(self.store).action_receive_quarantine(10.0)  # already received
        # the truck may leave before the lab has decided
        visit.action_clear_exit()
        visit.action_exit()
        self.assertEqual(visit.state, 'exited')
        self._results(inspection)
        inspection.with_user(self.manager).decide('accept_deduction', 2.0, 'Slight dust')
        self.assertEqual(inspection.payable_qty, round(4900.0 * 0.98, 1))
        self.assertEqual(self._qty(quarantine), 0.0)
        self.assertEqual(self._qty(self.stock), 4900.0)
        self.assertTrue(visit.pv_draft_id)

    def test_flow_b_rejection_blocks_the_goods(self):
        order = self._order(qty=3000.0)
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'FB-2', 'purchase_id': order.id, 'partner_id': self.supplier.id, 'flow': 'B'})
        inspection = visit.action_receive_quarantine(2900.0)
        self._results(inspection, moisture=17.0)
        inspection.with_user(self.manager).decide('reject', note='Mouldy')
        blocked = self.env['mos.stock.status.change'].status_location(self.site, 'blocked')
        self.assertEqual(self._qty(blocked), 2900.0)
        self.assertEqual(self._qty(self.stock), 0.0)
        self.assertEqual(visit.grn_state, 'rejected')
        self.assertFalse(self.env['mos.pv.draft'].search([('visit_id', '=', visit.id)]))

    def test_flow_b_checks_the_excess_policy_and_the_flow(self):
        order = self._order(qty=1000.0)
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'FB-3', 'purchase_id': order.id, 'partner_id': self.supplier.id, 'flow': 'B'})
        with self.assertRaises(UserError):
            visit.action_receive_quarantine(1200.0)  # 20 % over the order
        with self.assertRaises(UserError):
            visit.action_receive_quarantine(0)
        plain = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'FA-9', 'purchase_id': order.id})
        with self.assertRaises(UserError):
            plain.action_receive_quarantine(10.0)  # not a flow B visit

    # -- flow C ----------------------------------------------------------------------------------------------
    def test_flow_c_lab_opens_the_lot_and_the_receipt_follows(self):
        order = self._order()
        visit, inspection = self.env['mos.gate.visit'].with_user(self.analyst).lab_intake(
            order, 'FC-1', gross=48000.0, tare=14000.0, driver_name='Kebede', delivery_note='DN-7788', bags=0
        )
        self.assertEqual((visit.flow, visit.delivery_note, visit.state), ('C', 'DN-7788', 'weighed'))
        self.assertTrue(visit.ticket_id.provisional)
        self.assertEqual(visit.ticket_id.state, 'posted')
        self.assertEqual(visit.net_weight, 34000.0)
        self.assertEqual(inspection.state, 'open')
        self._results(inspection)
        inspection.with_user(self.manager).decide('accept')
        visit = visit.with_user(self.guard)
        picking = visit.action_prepare_grn()
        self.assertEqual(picking.move_ids.quantity, 34000.0)
        visit.with_user(self.store).action_confirm_grn()
        visit.action_clear_exit()
        visit.action_exit()
        self.assertEqual(visit.state, 'exited')

    def test_flow_c_validates_the_intake(self):
        order = self._order()
        with self.assertRaises(UserError):
            self.env['mos.gate.visit'].lab_intake(order, 'FC-2', gross=1000.0, tare=2000.0)
        draft = self.env['purchase.order'].create({'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10, 'price_unit': 1})]})
        with self.assertRaises(UserError):
            self.env['mos.gate.visit'].lab_intake(draft, 'FC-3', gross=2000.0, tare=1000.0)  # PO not confirmed

    # -- modes ------------------------------------------------------------------------------------------------
    def test_modes_are_a_singleton_and_audited(self):
        setting = self.env['mos.flow.setting']._singleton()
        self.assertEqual((setting.grn_mode, setting.pv_mode), ('confirm', 'draft'))
        with self.assertRaises(UserError):
            self.env['mos.flow.setting'].create({})
        with self.assertRaises(UserError):
            setting.unlink()
        before = self.env['mos.audit.event'].search_count([('res_model', '=', 'mos.flow.setting')])
        setting.grn_mode = 'automatic'
        self.assertGreater(self.env['mos.audit.event'].search_count([('res_model', '=', 'mos.flow.setting')]), before)
