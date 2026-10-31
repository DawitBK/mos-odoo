from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestStockReceipt(TransactionCase):
    """FR-INV-005 and FR-PLT-047: stock that arrives without an order or a transfer, with a reason (or as an opening balance) and a cost."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Receipt mill', 'code': 'RCPM', 'warehouse_id': cls.warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.manager = user('mr_manager', 'mos_inventory.group_mos_inventory_manager')
        cls.manager2 = user('mr_manager2', 'mos_inventory.group_mos_inventory_manager')
        cls.store = user('mr_store', 'mos_inbound.group_mos_storekeeper')
        cls.product = env['product.product'].create({'name': 'Gifted wheat', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 10.0})
        cls.lot_product = env['product.product'].create({'name': 'Gifted lot item', 'type': 'consu', 'is_storable': True, 'tracking': 'lot', 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 10.0})
        cls.stock = cls.warehouse.lot_stock_id
        cls.reason = env['mos.reason'].create({'domain': 'misc_receipt', 'code': 'GIFT', 'name': 'Donated by a supplier'})

    def _qty(self, product=None):
        return sum(self.env['stock.quant'].search([('product_id', '=', (product or self.product).id), ('location_id', '=', self.stock.id)]).mapped('quantity'))

    def _receipt(self, qty=10.0, cost=12.5, user=None, product=None, **extra):
        vals = {
            'site_id': self.site.id,
            'location_id': self.stock.id,
            'reason_id': self.reason.id,
            'line_ids': [Command.create({'product_id': (product or self.product).id, 'qty': qty, 'unit_cost': cost})],
        }
        return self.env['mos.stock.receipt'].with_user(user or self.manager).create({**vals, **extra})

    def test_a_receipt_puts_the_goods_in_stock_with_its_cost_and_needs_a_second_person_when_no_rule_applies(self):
        receipt = self._receipt()
        self.assertEqual(receipt.amount, 125.0)
        receipt.action_submit()
        self.assertEqual(receipt.state, 'approved')
        with self.assertRaises(UserError):
            receipt.action_receive()  # the requester posting their own receipt
        receipt.with_user(self.manager2).action_receive()
        self.assertEqual(receipt.state, 'received')
        self.assertEqual(self._qty(), 10.0)
        self.assertTrue(receipt.name.startswith('MR-'))
        move = self.env['stock.move'].search([('origin', '=', receipt.name)])
        self.assertEqual(move.price_unit, 12.5)

    def test_a_cost_and_a_quantity_are_required_and_a_miscellaneous_receipt_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            self._receipt(cost=0.0)
        with self.assertRaises(ValidationError):
            self._receipt(qty=0.0)
        with self.assertRaises(ValidationError):
            self._receipt(reason_id=False)
        opening = self._receipt(kind='opening', reason_id=False)
        self.assertEqual(opening.kind, 'opening')

    def test_a_receipt_cannot_be_dated_in_the_future(self):
        with self.assertRaises(ValidationError):
            self._receipt(date=fields.Date.context_today(self.env['mos.stock.receipt']) + timedelta(days=1))

    def test_a_storekeeper_cannot_make_one(self):
        with self.assertRaises(AccessError):
            self._receipt(user=self.store)

    def test_a_lot_is_made_with_its_dates_when_the_item_is_tracked(self):
        receipt = self.env['mos.stock.receipt'].with_user(self.manager).create(
            {
                'site_id': self.site.id,
                'location_id': self.stock.id,
                'kind': 'opening',
                'line_ids': [Command.create({'product_id': self.lot_product.id, 'qty': 5.0, 'unit_cost': 9.0, 'lot_name': 'OPEN-LOT-1', 'mfg_date': '2026-09-01', 'expiry_date': '2027-03-01'})],
            }
        )
        receipt.action_submit()
        receipt.with_user(self.manager2).action_receive()
        lot = self.env['stock.lot'].search([('name', '=', 'OPEN-LOT-1'), ('product_id', '=', self.lot_product.id)])
        self.assertEqual(str(lot.mos_expiry_date), '2027-03-01')
        self.assertEqual(self._qty(self.lot_product), 5.0)

    def test_the_matrix_can_ask_for_an_approval(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.stock.receipt', 'amount_from': 0.0, 'group_id': self.env.ref('mos_inventory.group_mos_inventory_manager').id})
        receipt = self._receipt()
        receipt.action_submit()
        self.assertEqual(receipt.state, 'pending')
        receipt.mos_approval_request_id.current_step_id.with_user(self.manager2).action_approve()
        self.assertEqual(receipt.state, 'approved')
        receipt.action_receive()
        self.assertEqual(receipt.state, 'received')

    def test_a_received_receipt_is_final(self):
        receipt = self._receipt()
        receipt.action_submit()
        receipt.with_user(self.manager2).action_receive()
        with self.assertRaises(UserError):
            receipt.action_cancel()
        with self.assertRaises(UserError):
            receipt.write({'note': 'edited after posting'})
