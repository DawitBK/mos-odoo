from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestInventoryControl(TransactionCase):
    """Adjustments with approval, cycle counts, status changes, reorder report, archive rules, labels."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLI', 'warehouse_id': cls.warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.store = user('istore', 'mos_inbound.group_mos_storekeeper')
        cls.store2 = user('istore2', 'mos_inbound.group_mos_storekeeper')
        cls.manager = user('imanager', 'mos_inventory.group_mos_inventory_manager')
        cls.dept = user('idept', 'mos_approvals.group_mos_dept_manager')
        cls.product = env['product.product'].create(
            {'name': 'Flour 50kg', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 30.0}
        )
        cls.stock = cls.warehouse.lot_stock_id
        cls._set_stock(cls.product, cls.stock, 1000.0)
        cls.reason = env['mos.reason'].create({'domain': 'adjustment', 'code': 'ADJ', 'name': 'Found on recount'})
        cls.status_reason = env['mos.reason'].create({'domain': 'status_change', 'code': 'QC', 'name': 'Suspected contamination'})

    @classmethod
    def _set_stock(cls, product, location, qty):
        quant = cls.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id, 'location_id': location.id, 'inventory_quantity': qty})
        quant.action_apply_inventory()

    def _qty(self, location, product=None):
        quants = self.env['stock.quant'].search([('product_id', '=', (product or self.product).id), ('location_id', '=', location.id)])
        return sum(quants.mapped('quantity'))

    def _adjustment(self, new_qty, user=None):
        adj = self.env['mos.stock.adjustment'].with_user(user or self.store).create(
            {
                'site_id': self.site.id,
                'location_id': self.stock.id,
                'reason_id': self.reason.id,
                'line_ids': [Command.create({'product_id': self.product.id, 'new_qty': new_qty})],
            }
        )
        return adj

    # -- adjustments -------------------------------------------------------------------------------------
    def test_adjustment_without_a_matching_rule_needs_a_second_person_to_apply(self):
        adj = self._adjustment(940.0)
        self.assertEqual(adj.line_ids.system_qty, 1000.0)
        self.assertEqual(adj.line_ids.delta, -60.0)
        self.assertEqual(adj.amount, 1800.0)
        adj.action_submit()
        self.assertEqual(adj.state, 'approved')
        with self.assertRaises(UserError):
            adj.with_user(self.store).action_apply()  # the requester applying their own
        adj.with_user(self.store2).action_apply()
        self.assertEqual(adj.state, 'applied')
        self.assertEqual(self._qty(self.stock), 940.0)
        with self.assertRaises(UserError):
            adj.write({'note': 'edited after applying'})

    def test_adjustment_above_the_threshold_goes_through_the_matrix(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.stock.adjustment', 'amount_from': 1000, 'group_id': self.env.ref('mos_approvals.group_mos_dept_manager').id})
        adj = self._adjustment(900.0)  # 100 kg * 30 = 3000
        adj.action_submit()
        self.assertEqual(adj.state, 'pending')
        with self.assertRaises(UserError):
            adj.action_apply()
        adj.mos_approval_request_id.current_step_id.with_user(self.dept).action_approve()
        self.assertEqual(adj.state, 'approved')
        adj.with_user(self.store).action_apply()
        self.assertEqual(self._qty(self.stock), 900.0)

    def test_adjustment_validation(self):
        with self.assertRaises(UserError):
            self._adjustment(1000.0).action_submit()  # nothing changes
        empty = self.env['mos.stock.adjustment'].with_user(self.store).create({'site_id': self.site.id, 'location_id': self.stock.id, 'reason_id': self.reason.id})
        with self.assertRaises(UserError):
            empty.action_submit()
        with self.assertRaises(ValidationError):
            self._adjustment(-5.0)
        adj = self._adjustment(990.0)
        adj.action_submit()
        with self.assertRaises(UserError):
            adj.line_ids.write({'new_qty': 1.0})  # locked once submitted

    # -- cycle counts ------------------------------------------------------------------------------------
    def test_cycle_count_creates_an_adjustment_for_differences(self):
        count = self.env['mos.cycle.count'].with_user(self.store).create({'site_id': self.site.id, 'location_id': self.stock.id})
        with self.assertRaises(UserError):
            count.action_submit()  # not started
        count.action_start()
        line = count.line_ids.filtered(lambda row: row.product_id == self.product)
        self.assertEqual(line.system_qty, 1000.0)
        with self.assertRaises(UserError):
            count.action_submit()  # not counted yet
        other = count.line_ids - line
        for row in other:
            row.counted_qty = row.system_qty
        line.counted_qty = 1000.0 * 0.999  # 0.1 % is inside the 0.5 % tolerance
        count.action_submit()
        self.assertFalse(count.adjustment_id)
        count2 = self.env['mos.cycle.count'].with_user(self.store).create({'site_id': self.site.id, 'location_id': self.stock.id})
        count2.action_start()
        for row in count2.line_ids:
            row.counted_qty = row.system_qty - (50.0 if row.product_id == self.product else 0.0)
        count2.action_submit()
        self.assertTrue(count2.adjustment_id)
        self.assertEqual(count2.adjustment_id.line_ids.delta, -50.0)
        with self.assertRaises(UserError):
            count2.action_close()  # the adjustment is still open
        count2.adjustment_id.with_user(self.store2).action_apply()
        count2.action_close()
        self.assertEqual(count2.state, 'closed')

    def test_cycle_count_needs_stock(self):
        empty_loc = self.env['stock.location'].create({'name': 'Empty shelf', 'location_id': self.warehouse.view_location_id.id, 'usage': 'internal'})
        count = self.env['mos.cycle.count'].create({'site_id': self.site.id, 'location_id': empty_loc.id})
        with self.assertRaises(UserError):
            count.action_start()

    # -- status changes -----------------------------------------------------------------------------------
    def test_quarantine_and_release(self):
        change = self.env['mos.stock.status.change'].with_user(self.store).create(
            {'site_id': self.site.id, 'product_id': self.product.id, 'qty': 200.0, 'from_status': 'available', 'to_status': 'quarantine', 'reason_id': self.status_reason.id}
        )
        with self.assertRaises(UserError):
            change.with_user(self.store).action_approve()  # not a manager
        with self.assertRaises(UserError):
            change.action_apply()  # not approved
        change.with_user(self.manager).action_approve()
        change.with_user(self.manager).action_apply()
        self.assertEqual(change.state, 'done')
        quarantine = self.env['mos.stock.status.change'].status_location(self.site, 'quarantine')
        self.assertEqual(quarantine.mos_stock_status, 'quarantine')
        self.assertEqual(self._qty(quarantine), 200.0)
        self.assertEqual(self._qty(self.stock), 800.0)
        release = self.env['mos.stock.status.change'].with_user(self.store).create(
            {'site_id': self.site.id, 'product_id': self.product.id, 'qty': 500.0, 'from_status': 'quarantine', 'to_status': 'available', 'reason_id': self.status_reason.id}
        )
        release.with_user(self.manager).action_approve()
        with self.assertRaises(UserError):
            release.with_user(self.manager).action_apply()  # only 200 kg is in quarantine
        with self.assertRaises(ValidationError):
            self.env['mos.stock.status.change'].create({'site_id': self.site.id, 'product_id': self.product.id, 'qty': 1, 'from_status': 'available', 'to_status': 'available', 'reason_id': self.status_reason.id})

    # -- reorder ------------------------------------------------------------------------------------------
    def test_reorder_report(self):
        self.product.product_tmpl_id.write({'mos_reorder_min': 1500.0, 'mos_reorder_qty': 2000.0})
        self.env.flush_all()
        row = self.env['mos.reorder.report'].search([('product_id', '=', self.product.id)])
        self.assertEqual(row.on_hand, 1000.0)
        self.assertEqual(row.shortfall, 500.0)
        self.assertEqual(row.suggested_qty, 2000.0)
        supplier = self.env['res.partner'].create({'name': 'Mill supplies', 'supplier_rank': 1})
        order = self.env['purchase.order'].create({'partner_id': supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 600.0, 'price_unit': 30.0})]})
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.env.flush_all()
        self.env.invalidate_all()  # the view rows were read before the order existed
        row = self.env['mos.reorder.report'].search([('product_id', '=', self.product.id)])
        self.assertEqual(row.on_order, 600.0)
        self.assertEqual(row.shortfall, 0.0)
        self.assertEqual(row.suggested_qty, 0.0)
        with self.assertRaises(ValidationError):
            self.product.product_tmpl_id.mos_reorder_min = -1

    def test_safety_stock_and_maximum_level_shape_the_reorder_report(self):
        template = self.product.product_tmpl_id
        template.write({'mos_safety_stock': 1200.0, 'mos_reorder_min': 1500.0, 'mos_reorder_qty': 2000.0, 'mos_max_level': 2200.0})
        self.env.flush_all()
        row = self.env['mos.reorder.report'].search([('product_id', '=', self.product.id)])
        self.assertTrue(row.below_safety, '1000 is below the safety stock of 1200')
        self.assertEqual(row.suggested_qty, 1200.0, 'a suggestion never takes the stock above the maximum: 2200 - 1000')
        with self.assertRaises(ValidationError):
            template.mos_safety_stock = 1600.0  # above the reorder level
        with self.assertRaises(ValidationError):
            template.mos_max_level = 1400.0  # below the reorder level

    # -- archive rules ------------------------------------------------------------------------------------
    def test_archiving_is_blocked_while_it_is_in_use(self):
        with self.assertRaises(UserError):
            self.product.action_archive()  # 1000 in stock
        supplier = self.env['res.partner'].create({'name': 'Busy supplier', 'supplier_rank': 1})
        order = self.env['purchase.order'].create({'partner_id': supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 10.0, 'price_unit': 30.0})]})
        with self.assertRaises(UserError):
            supplier.action_archive()  # open order
        order.button_cancel()
        order.unlink()
        supplier.action_archive()
        self.assertFalse(supplier.active)
        idle = self.env['product.product'].create({'name': 'Unused', 'type': 'consu'})
        idle.action_archive()
        self.assertFalse(idle.active)

    # -- labels -------------------------------------------------------------------------------------------
    def test_labels_render(self):
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_inventory.report_product_label', self.product.ids)
        self.assertIn(b'Flour 50kg', html)
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_inventory.report_location_label', self.stock.ids)
        self.assertIn(b'Available', html)
