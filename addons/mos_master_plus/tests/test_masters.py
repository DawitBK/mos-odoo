from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMasters(TransactionCase):
    """The warehouse, store, bin and item masters (client pages 16-20): the rules they state are enforced when stock moves."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].create({'name': 'Master test warehouse', 'code': 'MTW'})
        cls.kg = env.ref('uom.product_uom_kgm')
        cls.wheat = env['product.product'].create({'name': 'Master test wheat', 'type': 'consu', 'is_storable': True, 'uom_id': cls.kg.id})
        cls.supplier_location = env.ref('stock.stock_location_suppliers')
        cls.customer_location = env.ref('stock.stock_location_customers')
        cls.store = env['stock.location'].create({'name': 'Raw material area', 'mos_kind': 'store', 'mos_code': 'RM-A', 'mos_warehouse_id': cls.warehouse.id, 'mos_store_type': 'raw', 'mos_capacity_kg': 10000})
        cls.bin_a = env['stock.location'].create({'mos_kind': 'bin', 'mos_code': 'BIN-A', 'mos_warehouse_id': cls.warehouse.id, 'location_id': cls.store.id, 'mos_capacity_kg': 3000, 'mos_item_id': cls.wheat.id})
        cls.bin_b = env['stock.location'].create({'mos_kind': 'bin', 'mos_code': 'BIN-B', 'mos_warehouse_id': cls.warehouse.id, 'location_id': cls.store.id, 'mos_capacity_kg': 3000})

    def _move(self, source, destination, qty):
        picking_type = self.env['stock.picking.type'].search([('code', '=', 'internal'), ('warehouse_id', '=', self.warehouse.id)], limit=1)
        move = self.env['stock.move'].create(
            {'product_id': self.wheat.id, 'product_uom_qty': qty, 'uom_id': self.kg.id, 'location_id': source.id, 'location_dest_id': destination.id, 'picking_type_id': picking_type.id}
        )
        move._action_confirm()
        move.write({'quantity': qty, 'picked': True})
        move._action_done()
        return move

    # -- the records ----------------------------------------------------------------------------------------------------------------
    def test_a_store_sits_in_the_stock_location_of_its_warehouse_and_a_bin_in_a_store(self):
        self.assertEqual(self.store.location_id, self.warehouse.lot_stock_id)
        self.assertEqual((self.bin_a.name, self.bin_a.location_id, self.bin_a.warehouse_id), ('BIN-A', self.store, self.warehouse), 'the bin takes its code as its name')
        other = self.env['stock.warehouse'].create({'name': 'Another', 'code': 'ANO'})
        with self.assertRaises(ValidationError):
            self.env['stock.location'].create({'mos_kind': 'bin', 'mos_code': 'BIN-X', 'mos_warehouse_id': other.id, 'location_id': self.store.id})
        with self.assertRaises(ValidationError):
            self.env['stock.location'].create({'mos_kind': 'bin', 'mos_code': 'BIN-Y', 'mos_warehouse_id': self.warehouse.id, 'location_id': self.warehouse.lot_stock_id.id})

    def test_a_code_is_used_once_in_a_warehouse(self):
        with self.assertRaises(Exception):
            self.env['stock.location'].create({'mos_kind': 'bin', 'mos_code': 'BIN-A', 'mos_warehouse_id': self.warehouse.id, 'location_id': self.store.id})

    # -- what the warehouse allows ----------------------------------------------------------------------------------------------------
    def test_a_receipt_is_refused_where_the_warehouse_does_not_allow_receipts(self):
        self._move(self.supplier_location, self.bin_a, 100)
        self.warehouse.mos_allow_receipt = False
        with self.assertRaisesRegex(UserError, 'Receipts are not allowed'):
            self._move(self.supplier_location, self.bin_a, 100)
        self.warehouse.mos_allow_receipt = True
        self.bin_a.mos_allow_receipt = False
        with self.assertRaisesRegex(UserError, 'Receipts are not allowed'):
            self._move(self.supplier_location, self.bin_a, 100)

    def test_an_issue_and_a_transfer_follow_the_flags_of_the_place_they_leave(self):
        self._move(self.supplier_location, self.bin_a, 500)
        self.store.mos_allow_transfer = False
        with self.assertRaisesRegex(UserError, 'Transfers are not allowed'):
            self._move(self.bin_a, self.bin_b, 100)
        self.store.mos_allow_transfer = True
        self._move(self.bin_a, self.bin_b, 100)
        self.bin_a.mos_allow_issue = False
        with self.assertRaisesRegex(UserError, 'Issues are not allowed'):
            self._move(self.bin_a, self.customer_location, 50)
        self.bin_a.mos_allow_issue = True
        self._move(self.bin_a, self.customer_location, 50)

    # -- capacity -------------------------------------------------------------------------------------------------------------------
    def test_nothing_goes_into_a_bin_beyond_its_capacity(self):
        self._move(self.supplier_location, self.bin_a, 2500)
        self.assertEqual((self.bin_a.mos_occupied_kg, round(self.bin_a.mos_occupied_pct, 1)), (2500, 83.3))
        with self.assertRaisesRegex(UserError, 'does not fit'):
            self._move(self.supplier_location, self.bin_a, 600)
        self._move(self.supplier_location, self.bin_a, 500)  # exactly full
        self.assertEqual(self.bin_a.mos_occupied_kg, 3000)

    def test_the_store_and_the_warehouse_have_a_capacity_too_and_a_move_inside_does_not_count_twice(self):
        self._move(self.supplier_location, self.bin_a, 3000)
        self._move(self.supplier_location, self.bin_b, 3000)
        self.assertEqual(self.store.mos_occupied_kg, 6000, 'the store holds what its bins hold')
        self.store.mos_capacity_kg = 6500
        other = self.env['stock.location'].create({'mos_kind': 'bin', 'mos_code': 'BIN-C', 'mos_warehouse_id': self.warehouse.id, 'location_id': self.store.id, 'mos_capacity_kg': 3000})
        with self.assertRaisesRegex(UserError, 'does not fit'):
            self._move(self.supplier_location, other, 600)  # the bin has room, the store has not
        self.warehouse.mos_capacity_kg = 6100
        self.store.mos_capacity_kg = 0
        with self.assertRaisesRegex(UserError, 'does not fit'):
            self._move(self.supplier_location, other, 200)  # the warehouse is nearly full
        self._move(self.supplier_location, other, 100)

    def test_a_place_without_a_capacity_is_not_limited(self):
        self._move(self.supplier_location, self.bin_b, 1000)
        self.bin_b.mos_capacity_kg = 0
        self._move(self.supplier_location, self.bin_b, 5000)

    # -- the item master ------------------------------------------------------------------------------------------------------------------
    def test_an_item_gets_a_material_id_and_whether_it_is_inspected_is_kept_in_the_qc_policy(self):
        item = self.wheat.product_tmpl_id
        self.assertRegex(item.mos_material_id, r'^MAT\d{6}$')
        other = self.env['product.template'].create({'name': 'Second item', 'type': 'consu'})
        self.assertNotEqual(other.mos_material_id, item.mos_material_id)
        self.assertTrue(item.mos_inspection_required, 'inspected unless a policy says not')
        item.mos_inspection_required = False
        policy = self.env['mos.lab.policy'].search([('product_id', '=', self.wheat.id)])
        self.assertEqual((len(policy), policy.inspection_required), (1, False))
        item.mos_inspection_required = True
        self.assertTrue(policy.inspection_required)
        self.assertEqual(len(self.env['mos.lab.policy'].search([('product_id', '=', self.wheat.id)])), 1, 'one policy, never a second one')
