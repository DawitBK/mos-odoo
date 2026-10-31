from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestRequestDepartments(TransactionCase):
    """FR-PLT-007: documents record the requesting department and, for orders, the purchasing department."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Dept mill', 'code': 'DPTM', 'warehouse_id': cls.warehouse.id})
        cls.dept = env['mos.department'].create({'name': 'Packing'})
        cls.buyer = new_test_user(env, login='dept_buyer', groups='mos_procurement.group_mos_procurement_user')
        cls.buyer.write({'mos_site_ids': [Command.set(cls.site.ids)], 'mos_department_id': cls.dept.id})
        cls.product = env['product.product'].create({'name': 'Dept resin', 'type': 'consu', 'is_storable': True, 'standard_price': 1.0})

    def _request(self, **extra):
        vals = {'site_id': self.site.id, 'reason': 'Needed', 'line_ids': [Command.create({'product_id': self.product.id, 'qty': 5, 'estimated_price': 1.0})]}
        return self.env['mos.purchase.request'].with_user(self.buyer).create({**vals, **extra})

    def test_a_request_starts_in_the_requesters_home_department(self):
        req = self._request()
        self.assertEqual(req.department_id, self.dept)
        self.assertEqual(req.department, 'Packing')

    def test_choosing_another_department_updates_the_name(self):
        other = self.env['mos.department'].create({'name': 'Dispatch'})
        req = self._request(department_id=other.id)
        self.assertEqual(req.department, 'Dispatch')

    def test_a_name_written_as_text_finds_or_makes_the_department(self):
        req = self._request(department='Packing')
        self.assertEqual(req.department_id, self.dept)
        # Only an administrator or the system makes a new department from text; anyone else's text is kept as text.
        req2 = self._request(department='Cleaning')
        self.assertFalse(req2.department_id)
        self.assertEqual(req2.department, 'Cleaning')
        req3 = self.env['mos.purchase.request'].sudo().create({'site_id': self.site.id, 'reason': 'Needed', 'department': 'Laundry', 'line_ids': [Command.create({'product_id': self.product.id, 'qty': 1, 'estimated_price': 1.0})]})
        self.assertEqual(req3.department_id.name, 'Laundry')

    def test_an_order_carries_the_purchasing_department(self):
        supplier = self.env['res.partner'].create({'name': 'Dept supplier', 'supplier_rank': 1})
        order = self.env['purchase.order'].with_user(self.buyer).create({'partner_id': supplier.id})
        self.assertEqual(order.mos_department_id, self.dept)
        self.assertEqual(order.mos_department, 'Packing')
