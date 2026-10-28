from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestRequestForms(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Req mill', 'code': 'REQM', 'warehouse_id': cls.warehouse.id})
        cls.buyer = new_test_user(env, login='rf_buyer', groups='mos_procurement.group_mos_procurement_user')
        cls.buyer.mos_site_ids = [Command.set(cls.site.ids)]
        cls.buyer.mos_employee_ref = 'EMP-567'
        cls.product = env['product.product'].create({'name': 'Req resin', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 1.25})
        cls.supplier = env['res.partner'].create({'name': 'Req supplier', 'supplier_rank': 1})

    def _stock(self, qty, location=None):
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create(
            {'product_id': self.product.id, 'location_id': (location or self.warehouse.lot_stock_id).id, 'inventory_quantity': qty}
        )
        quant.action_apply_inventory()

    def _request(self, qty=500.0, **extra):
        return self.env['mos.purchase.request'].with_user(self.buyer).create(
            {'site_id': self.site.id, 'reason': 'Upcoming run', 'line_ids': [Command.create({'product_id': self.product.id, 'qty': qty, 'estimated_price': 1.25})], **extra}
        )

    def test_header_defaults_like_the_client_screen(self):
        req = self._request()
        self.assertEqual(req.request_date, fields.Date.context_today(req))
        self.assertEqual(req.priority, 'medium')
        self.assertEqual(req.requester_ref, 'EMP-567', 'taken from the person')
        self.assertEqual(req.line_count, 1)

    def test_the_required_date_cannot_be_before_the_request_date(self):
        with self.assertRaises(ValidationError):
            self._request(needed_by=fields.Date.today() - timedelta(days=3))

    def test_the_quantity_to_purchase_is_the_need_less_the_stock(self):
        self._stock(120.0)
        line = self._request(500.0).line_ids
        self.assertEqual(line.available_qty, 120.0)
        self.assertEqual(line.qty_to_purchase, 380.0, 'a request for 500 with 120 available proposes 380')
        self.assertEqual(line.line_total, 625.0)

    def test_stock_that_is_quarantined_is_not_available(self):
        quarantine = self.env['stock.location'].search([('mos_stock_status', '=', 'quarantine'), ('warehouse_id', '=', self.warehouse.id)], limit=1)
        if not quarantine:
            quarantine = self.env['stock.location'].create({'name': 'RF-Q', 'location_id': self.warehouse.view_location_id.id, 'usage': 'internal', 'mos_stock_status': 'quarantine'})
        self._stock(300.0, quarantine)
        line = self._request(500.0).line_ids
        self.assertEqual((line.available_qty, line.qty_to_purchase), (0.0, 500.0))

    def test_it_can_be_raised_with_a_reason_and_never_lowered(self):
        self._stock(120.0)
        line = self._request(500.0).line_ids
        with self.assertRaises(ValidationError):
            line.with_user(self.buyer).write({'qty_to_purchase': 300.0})  # below what is needed
        with self.assertRaises(ValidationError):
            line.with_user(self.buyer).write({'qty_to_purchase': 400.0})  # raised, no reason
        line.with_user(self.buyer).write({'qty_to_purchase': 400.0, 'increase_reason': 'Safety margin for the holiday'})
        self.assertEqual(line.qty_to_purchase, 400.0)

    def test_the_order_asks_for_the_quantity_to_purchase(self):
        self._stock(120.0)
        req = self._request(500.0)
        req.action_submit()
        order = req.action_convert(self.supplier)
        self.assertEqual(order.order_line.product_qty, 380.0)
        self.assertEqual(order.mos_department, req.department)

    def test_nothing_is_ordered_when_stock_covers_the_request(self):
        self._stock(900.0)
        req = self._request(500.0)
        req.action_submit()
        self.assertEqual(req.line_ids.qty_to_purchase, 0.0)
        with self.assertRaises(UserError):
            req.action_convert(self.supplier)

    def test_open_orders_can_count_as_coming_in(self):
        order = self.env['purchase.order'].create({'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 200.0, 'price_unit': 1.0})]})
        order.button_confirm()
        self.env['ir.config_parameter'].sudo().set_str('mos_request_forms.subtract_open_orders', 'True')
        line = self._request(500.0).line_ids
        self.assertEqual(line.qty_to_purchase, 300.0, 'the 200 already on order are not ordered twice')
