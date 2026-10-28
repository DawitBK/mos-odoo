from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestDeliveries(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        cls.kg = cls.env.ref('uom.product_uom_kgm')
        cls.tonne = cls.env.ref('uom.product_uom_ton')
        cls.bran = cls.env['product.product'].create({'name': 'Entry bran', 'type': 'consu', 'is_storable': True, 'uom_id': cls.kg.id, 'standard_price': 5.0})

    def _entry(self, order=None, **extra):
        order = order or self._order()
        return self.env['mos.gate.visit'].with_user(self.guard).create(
            {'site_id': self.site.id, 'plate': 'DE-0001', 'purchase_id': order.id, 'partner_id': order.partner_id.id, 'delivery_note': 'DN-77', **extra}
        )

    def _line(self, visit, product, qty, uom=None, **extra):
        return self.env['mos.gate.visit.line'].with_user(self.guard).create(
            {'visit_id': visit.id, 'product_id': product.id, 'declared_qty': qty, 'uom_id': (uom or product.uom_id).id, **extra}
        )

    def test_items_come_from_the_order_and_show_what_is_due(self):
        order = self._order()
        visit = self._entry(order)
        wheat = order.order_line[0].product_id
        line = self._line(visit, wheat, 1000.0)
        self.assertEqual(line.ordered_qty, order.order_line[0].product_uom_qty)
        self.assertEqual(line.outstanding_qty, line.ordered_qty, 'nothing received yet')
        self.assertFalse(line.over_order)
        over = self._line(visit, wheat, line.ordered_qty * 2, note='too much')
        self.assertTrue(over.over_order, 'flagged, not refused: the gate can still decide')

    def test_an_item_that_is_not_on_the_order_is_refused(self):
        visit = self._entry()
        with self.assertRaises(ValidationError):
            self._line(visit, self.bran, 500.0)

    def test_the_declared_quantity_must_be_above_zero(self):
        order = self._order()
        visit = self._entry(order)
        with self.assertRaises(ValidationError):
            self._line(visit, order.order_line[0].product_id, 0.0)

    def test_totals_are_one_per_unit_never_one_number_for_tonnes_and_kilograms(self):
        order = self._order()
        visit = self._entry(order)
        wheat = order.order_line[0].product_id
        self._line(visit, wheat, 25.0, uom=self.tonne, bags=500)
        self._line(visit, wheat, 500.0, uom=self.kg, bags=10)
        visit.invalidate_recordset()
        totals = str(visit.declared_totals)
        self.assertIn('25.000</strong> t', totals.replace('Tonne', 't').replace('Ton', 't'))
        self.assertIn('500.000</strong> kg', totals)
        self.assertNotIn('525', totals, 'the units are never added together')
        self.assertIn('510</strong> bags', totals)

    def test_approving_sends_the_delivery_on_and_needs_items_and_a_matching_supplier(self):
        order = self._order()
        visit = self._entry(order)
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_entry_approve()  # no items yet
        self._line(visit, order.order_line[0].product_id, 1000.0)
        other = self.env['res.partner'].create({'name': 'Someone else', 'supplier_rank': 1})
        visit.write({'partner_id': other.id})
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_entry_approve()  # not the order's supplier
        visit.write({'partner_id': order.partner_id.id})
        visit.with_user(self.guard).action_entry_approve()
        self.assertEqual(visit.entry_state, 'approved')
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_entry_approve()  # once

    def test_a_delivery_without_an_order_cannot_be_approved(self):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'DE-0002'})
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_entry_approve()

    def test_rejecting_needs_a_reason_and_closes_the_visit(self):
        order = self._order()
        visit = self._entry(order)
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_entry_reject('  ')
        dialog = self.env['mos.delivery.reject'].with_user(self.guard).create({'visit_id': visit.id, 'reason': 'Wrong supplier on the note'})
        dialog.action_confirm()
        self.assertEqual((visit.entry_state, visit.state), ('rejected', 'cancelled'))
        self.assertEqual(visit.entry_reject_reason, 'Wrong supplier on the note')
        self.assertTrue(self.env['mos.audit.event'].sudo().search([('action', '=', 'delivery_entry_rejected'), ('res_id', '=', visit.id)]))

    def test_the_approval_before_weighing_is_a_setting_that_is_off_by_default(self):
        order = self._order()
        visit = self._entry(order)
        visit.with_user(self.guard).action_start_weighing()
        self.assertEqual(visit.state, 'weighing', 'off by default')
        self.env['ir.config_parameter'].sudo().set_str('mos_deliveries.require_entry_approval', 'True')
        second = self._entry(self._order(), plate='DE-0003')
        with self.assertRaises(UserError):
            second.with_user(self.guard).action_start_weighing()
        self._line(second, second.purchase_id.order_line[0].product_id, 100.0)
        second.with_user(self.guard).action_entry_approve()
        second.with_user(self.guard).action_start_weighing()
        self.assertEqual(second.state, 'weighing')

    def test_outbound_visits_have_no_entry_to_approve(self):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'DE-0004', 'purpose': 'outbound_do'})
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_entry_approve()
