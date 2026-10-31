from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestStockStates(TransactionCase):
    """FR-INV-002, 003, 011: five stock statuses with their exits, and stock in a store is never negative."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'States mill', 'code': 'STSM', 'warehouse_id': cls.warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.store = user('ss_store', 'mos_inbound.group_mos_storekeeper')
        cls.manager = user('ss_manager', 'mos_inventory.group_mos_inventory_manager')
        cls.product = env['product.product'].create({'name': 'Stateful wheat', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 10.0})
        cls.stock = cls.warehouse.lot_stock_id
        quant = env['stock.quant'].with_context(inventory_mode=True).create({'product_id': cls.product.id, 'location_id': cls.stock.id, 'inventory_quantity': 100.0})
        quant.action_apply_inventory()
        cls.reason = env['mos.reason'].create({'domain': 'status_change', 'code': 'SS-DMG', 'name': 'Water damage'})
        cls.scrap_reason = env['mos.reason'].create({'domain': 'scrap', 'code': 'SS-SCR', 'name': 'Beyond use'})

    def _total(self):
        return sum(self.env['stock.quant'].search([('product_id', '=', self.product.id), ('location_id.usage', '=', 'internal')]).mapped('quantity'))

    def _change(self, qty, frm, to):
        return self.env['mos.stock.status.change'].with_user(self.store).create(
            {'site_id': self.site.id, 'product_id': self.product.id, 'qty': qty, 'from_status': frm, 'to_status': to, 'reason_id': self.reason.id}
        )

    def _apply(self, change):
        change.with_user(self.manager).action_approve()
        change.with_user(self.manager).action_apply()

    def test_damaging_stock_moves_it_to_a_damaged_place_and_leaves_the_total_alone(self):
        before = self._total()
        change = self._change(20.0, 'available', 'damaged')
        self._apply(change)
        self.assertEqual(self._total(), before)
        damaged = self.env['mos.stock.status.change'].status_location(self.site, 'damaged')
        self.assertEqual(damaged.mos_stock_status, 'damaged')
        self.assertEqual(sum(self.env['stock.quant'].search([('product_id', '=', self.product.id), ('location_id', '=', damaged.id)]).mapped('quantity')), 20.0)

    def test_damaged_stock_can_be_recovered_but_not_moved_to_another_hold(self):
        self._apply(self._change(10.0, 'available', 'damaged'))
        with self.assertRaises(ValidationError):
            self._change(5.0, 'damaged', 'blocked')
        self._apply(self._change(5.0, 'damaged', 'available'))

    def test_rejected_stock_has_no_status_exit_and_only_quarantine_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._change(5.0, 'available', 'rejected')
        self._apply(self._change(10.0, 'available', 'quarantine'))
        self._apply(self._change(10.0, 'quarantine', 'rejected'))
        with self.assertRaises(ValidationError):
            self._change(5.0, 'rejected', 'available')

    def test_damaged_stock_can_be_scrapped_but_not_consumed(self):
        self._apply(self._change(10.0, 'available', 'damaged'))
        damaged = self.env['mos.stock.status.change'].status_location(self.site, 'damaged')
        line = [Command.create({'product_id': self.product.id, 'qty': 4.0})]
        with self.assertRaises(ValidationError):
            self.env['mos.stock.issue'].with_user(self.store).create({'site_id': self.site.id, 'location_id': damaged.id, 'department_id': self.env['mos.department'].create({'name': 'SS dept'}).id, 'line_ids': line})
        scrap = self.env['mos.stock.issue'].with_user(self.store).create({'site_id': self.site.id, 'location_id': damaged.id, 'kind': 'scrap', 'reason_id': self.scrap_reason.id, 'line_ids': line})
        scrap.action_submit()
        scrap.with_user(self.manager).action_issue()
        self.assertEqual(scrap.state, 'issued')

    def test_stock_in_a_store_is_never_negative_but_virtual_places_may_be(self):
        quant = self.env['stock.quant']
        empty = self.env['stock.location'].create({'name': 'Empty shelf', 'usage': 'internal', 'location_id': self.warehouse.view_location_id.id})
        with self.assertRaises(ValidationError):
            quant._update_available_quantity(self.product, empty, -5.0)
        supplier = self.env.ref('stock.stock_location_suppliers')
        quant._update_available_quantity(self.product, supplier, -5.0)
        self.assertLess(sum(quant.search([('product_id', '=', self.product.id), ('location_id', '=', supplier.id)]).mapped('quantity')), 0)

    def test_issuing_more_than_is_there_through_a_transfer_is_refused(self):
        other = self.env['stock.location'].create({'name': 'Other shelf', 'usage': 'internal', 'location_id': self.warehouse.view_location_id.id})
        move = self.env['stock.move'].create({'product_id': self.product.id, 'product_uom_qty': 500.0, 'uom_id': self.product.uom_id.id, 'location_id': self.stock.id, 'location_dest_id': other.id, 'quantity': 500.0, 'picked': True})
        move._action_confirm()
        with self.assertRaises((ValidationError, UserError)):
            move._action_done()
