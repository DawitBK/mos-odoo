from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestStockPlus(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        env = cls.env
        cls.wh1 = cls.site.warehouse_id
        cls.wh2 = env['stock.warehouse'].create({'name': 'Second depot', 'code': 'SD2'})
        cls.site2 = env['mos.site'].create({'name': 'Depot', 'code': 'DEP2', 'warehouse_id': cls.wh2.id})
        cls.store2 = new_test_user(env, login='xstore2', groups='mos_inbound.group_mos_storekeeper')
        cls.store2.mos_site_ids = [Command.set(cls.site2.ids)]
        cls.both = new_test_user(env, login='xboth', groups='mos_inbound.group_mos_storekeeper')
        cls.both.mos_site_ids = [Command.set((cls.site | cls.site2).ids)]
        kg = env.ref('uom.product_uom_kgm')
        cls.grain = env['product.category'].create({'name': 'Grain MOS'})
        cls.wheat.categ_id = cls.grain
        cls.lotted = env['product.product'].create({'categ_id': cls.grain.id, 'name': 'Lot wheat', 'type': 'consu', 'is_storable': True, 'tracking': 'lot', 'uom_id': kg.id, 'standard_price': 20.0})
        cls.reason = env['mos.reason'].create({'domain': 'transit_loss', 'code': 'TLX', 'name': 'Lost on the road'})

    @classmethod
    def _stock(cls, product, qty, location, lot=None):
        vals = {'product_id': product.id, 'location_id': location.id, 'inventory_quantity': qty}
        if lot:
            vals['lot_id'] = lot.id
        cls.env['stock.quant'].with_context(inventory_mode=True).create(vals).action_apply_inventory()

    def _on_hand(self, product, location, lot=None):
        domain = [('product_id', '=', product.id), ('location_id', 'child_of', location.id)] + ([('lot_id', '=', lot.id)] if lot else [])
        return sum(self.env['stock.quant'].sudo().search(domain).mapped('quantity'))

    # -- lots ---------------------------------------------------------------------------------------------------------
    def test_lot_numbers_follow_the_batch_pattern_without_repeating(self):
        lot_model = self.env['stock.lot']
        today = fields.Date.today().strftime('%Y%m%d')
        a = lot_model.create({'product_id': self.lotted.id})
        b = lot_model.create({'product_id': self.lotted.id})
        self.assertRegex(a.name, rf'^BATCH-{today}-\d\d$')
        self.assertEqual(int(b.name[-2:]), int(a.name[-2:]) + 1)
        own = lot_model.create({'product_id': self.lotted.id, 'name': 'SUP-123', 'mos_supplier_lot': 'SUP-123'})
        self.assertEqual(own.name, 'SUP-123', "a supplier's own number is kept")
        self.assertNotEqual(a.name, b.name)

    def test_lot_dates_and_attributes_are_checked(self):
        category = self.lotted.categ_id
        moisture = self.env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Moisture', 'kind': 'number', 'min_value': 8, 'max_value': 16, 'required': True})
        origin = self.env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Origin', 'kind': 'choice', 'choices': 'Arsi, Bale, Gojjam'})
        lot = self.env['stock.lot'].create({'product_id': self.lotted.id, 'mos_mfg_date': '2026-01-10', 'mos_expiry_date': '2026-12-31'})
        with self.assertRaises(ValidationError):
            lot.mos_expiry_date = '2025-12-31'
        self.env['mos.lot.attribute.value'].create({'lot_id': lot.id, 'attribute_id': moisture.id, 'value': '12.5'})
        with self.assertRaises(ValidationError):
            self.env['mos.lot.attribute.value'].create({'lot_id': lot.id, 'attribute_id': origin.id, 'value': 'Tigray'})
        with self.assertRaises(ValidationError):
            lot.mos_attribute_ids.value = '30'
        with self.assertRaises(ValidationError):
            lot.mos_attribute_ids.value = 'wet'
        self.env['mos.lot.attribute.value'].create({'lot_id': lot.id, 'attribute_id': origin.id, 'value': 'Arsi'})
        self.assertFalse(lot._mos_missing_attributes())
        self.env['ir.config_parameter'].sudo().set_str('mos_stock_plus.enforce_lot_attributes', 'True')
        with self.assertRaises(ValidationError):
            self.env['stock.lot'].create({'product_id': self.lotted.id})  # no moisture recorded
        with self.assertRaises(ValidationError):
            self.env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Grade', 'kind': 'choice'})

    def test_split_keeps_the_stock_and_the_family(self):
        lot = self.env['stock.lot'].create({'product_id': self.lotted.id, 'mos_expiry_date': fields.Date.today() + timedelta(days=60), 'mos_supplier_lot': 'S-9'})
        self._stock(self.lotted, 1000.0, self.wh1.lot_stock_id, lot)
        part = lot.action_split(300.0, 'PART-A')
        self.assertEqual((part.name, part.mos_parent_lot_id, part.mos_expiry_date, part.mos_supplier_lot), ('PART-A', lot, lot.mos_expiry_date, 'S-9'))
        self.assertEqual(self._on_hand(self.lotted, self.wh1.lot_stock_id, lot), 700.0)
        self.assertEqual(self._on_hand(self.lotted, self.wh1.lot_stock_id, part), 300.0)
        self.assertEqual(self._on_hand(self.lotted, self.wh1.lot_stock_id), 1000.0, 'the total is unchanged')
        sub = part.action_split(100.0)
        self.assertEqual(set(sub.action_trace().ids), {lot.id, part.id, sub.id}, 'the trace runs to the root and back down')
        with self.assertRaises(UserError):
            lot.action_split(700.0)  # all of it is not a split
        with self.assertRaises(UserError):
            lot.action_split(0)

    def test_expiry_list(self):
        soon = self.env['stock.lot'].create({'product_id': self.lotted.id, 'mos_expiry_date': fields.Date.today() + timedelta(days=10)})
        later = self.env['stock.lot'].create({'product_id': self.lotted.id, 'mos_expiry_date': fields.Date.today() + timedelta(days=90)})
        self._stock(self.lotted, 500.0, self.wh1.lot_stock_id, soon)
        self._stock(self.lotted, 800.0, self.wh1.lot_stock_id, later)
        rows = self.env['mos.lot.expiry'].expiring(30)
        self.assertEqual(rows.mapped('lot_id'), soon)
        self.assertEqual((rows.days_left, rows.qty), (10, 500.0))
        self.assertEqual(soon.mos_days_to_expiry, 10)
        self.assertEqual(soon.mos_status, 'available')
        empty = self.env['stock.lot'].create({'product_id': self.lotted.id})
        self.assertEqual(empty.mos_status, 'none')

    # -- transfers ----------------------------------------------------------------------------------------------------
    def _transfer(self, qty=1000.0, user=None):
        order = self.env['mos.transfer.order'].with_user(user or self.store).create({'from_site_id': self.site.id, 'to_site_id': self.site2.id, 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': qty})]})
        return order

    def test_transfer_moves_stock_through_transit(self):
        self._stock(self.wheat, 5000.0, self.wh1.lot_stock_id)
        order = self._transfer()
        self.assertTrue(order.name.startswith('TRF-'))
        order.with_user(self.store).action_dispatch()
        transit = self.env.ref('mos_stock_plus.location_mos_transit')
        self.assertEqual(order.state, 'dispatched')
        self.assertEqual(self._on_hand(self.wheat, self.wh1.lot_stock_id), 4000.0)
        self.assertEqual(self._on_hand(self.wheat, transit), 1000.0)
        self.assertEqual(self._on_hand(self.wheat, self.wh2.lot_stock_id), 0.0)
        with self.assertRaises(UserError):
            order.line_ids.write({'qty': 5})  # frozen once dispatched
        order.with_user(self.store2).action_receive()
        self.assertEqual((order.state, order.received_by_id), ('received', self.store2))
        self.assertEqual(self._on_hand(self.wheat, transit), 0.0)
        self.assertEqual(self._on_hand(self.wheat, self.wh2.lot_stock_id), 1000.0)
        self.assertEqual(order.line_ids.received_qty, 1000.0)
        with self.assertRaises(UserError):
            order.with_user(self.store2).action_receive()  # already received

    def test_transfer_shortfall_needs_a_reason_and_is_written_off(self):
        self._stock(self.wheat, 5000.0, self.wh1.lot_stock_id)
        order = self._transfer()
        order.with_user(self.store).action_dispatch()
        line = order.line_ids
        with self.assertRaises(UserError):
            order.with_user(self.store2).action_receive({line.id: 900.0})  # 100 kg short, no reason
        with self.assertRaises(UserError):
            order.with_user(self.store2).action_receive({line.id: 1100.0}, self.reason)  # more than was sent
        order.with_user(self.store2).action_receive({line.id: 900.0}, self.reason)
        transit = self.env.ref('mos_stock_plus.location_mos_transit')
        self.assertEqual(self._on_hand(self.wheat, self.wh2.lot_stock_id), 900.0)
        self.assertEqual(self._on_hand(self.wheat, transit), 0.0, 'nothing is left in transit')
        self.assertEqual((order.loss_reason_id, bool(order.loss_picking_id)), (self.reason, True))

    def test_transfer_controls(self):
        self._stock(self.wheat, 500.0, self.wh1.lot_stock_id)
        with self.assertRaises(ValidationError):
            self.env['mos.transfer.order'].create({'from_site_id': self.site.id, 'to_site_id': self.site.id})
        too_much = self._transfer(qty=800.0)
        with self.assertRaises(UserError):
            too_much.with_user(self.store).action_dispatch()  # only 500 there
        with self.assertRaises(UserError):
            self._transfer(qty=100.0, user=self.requester).with_user(self.requester).action_dispatch()  # not a storekeeper
        with self.assertRaises(UserError):
            self._transfer(qty=100.0).with_user(self.store2).action_dispatch()  # does not work at the source
        order = self._transfer(qty=100.0, user=self.both)
        order.with_user(self.both).action_dispatch()
        with self.assertRaises(UserError):
            order.with_user(self.both).action_receive()  # the dispatcher cannot also receive
        with self.assertRaises(UserError):
            order.with_user(self.store).action_receive()  # works at the source, not the destination
        empty = self.env['mos.transfer.order'].with_user(self.store).create({'from_site_id': self.site.id, 'to_site_id': self.site2.id})
        with self.assertRaises(UserError):
            empty.action_dispatch()  # nothing to send

    def test_recalling_a_dispatched_transfer_returns_the_stock(self):
        self._stock(self.wheat, 2000.0, self.wh1.lot_stock_id)
        order = self._transfer(qty=600.0)
        order.with_user(self.store).action_dispatch()
        self.assertEqual(self._on_hand(self.wheat, self.wh1.lot_stock_id), 1400.0)
        order.with_user(self.store).action_cancel()
        self.assertEqual((order.state, self._on_hand(self.wheat, self.wh1.lot_stock_id)), ('cancelled', 2000.0))
        draft = self._transfer()
        draft.action_cancel()
        self.assertEqual(draft.state, 'cancelled')
        with self.assertRaises(UserError):
            draft.action_cancel()

    def test_late_transfers_raise_an_exception(self):
        self._stock(self.wheat, 2000.0, self.wh1.lot_stock_id)
        order = self._transfer(qty=100.0)
        order.with_user(self.store).action_dispatch()
        self.assertEqual(self.env['mos.transfer.order']._cron_flag_late(), 0, 'just sent')
        order.sudo().write({'dispatched_at': fields.Datetime.now() - timedelta(days=5)})
        order.invalidate_recordset(['days_in_transit'])
        self.assertEqual(self.env['mos.transfer.order']._cron_flag_late(), 1)
        exception = self.env['mos.exception'].search([('kind', '=', 'transit_late'), ('res_id', '=', order.id)])
        self.assertEqual(len(exception), 1)
        self.env['mos.transfer.order']._cron_flag_late()
        self.assertEqual(self.env['mos.exception'].search_count([('kind', '=', 'transit_late'), ('res_id', '=', order.id)]), 1, 'one exception, not one per night')

    # -- categories ---------------------------------------------------------------------------------------------------
    def test_supplier_categories(self):
        transport = self.env['mos.service.category'].create({'name': 'Transport', 'kind': 'service'})
        self.s1.mos_service_category_ids = [Command.link(transport.id)]
        self.s1.mos_supplied_category_ids = [Command.link(self.wheat.categ_id.id)]
        found = self.env['res.partner'].search([('mos_service_category_ids', 'in', transport.id)])
        self.assertEqual(found, self.s1)
        with self.assertRaises(Exception):
            self.env['mos.service.category'].create({'name': 'Transport'})
