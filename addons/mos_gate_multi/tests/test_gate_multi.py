from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestGateMulti(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        kg = cls.env.ref('uom.product_uom_kgm')
        cls.additive = cls.env['product.product'].create({'name': 'Improver MG', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 10.0})

    def _extra_order(self, qty=2000.0):
        order = self.env['purchase.order'].with_user(self.buyer).create({'partner_id': self.s2.id, 'order_line': [Command.create({'product_id': self.additive.id, 'product_qty': qty, 'price_unit': 10.0})]})
        order.with_user(self.buyer).button_confirm()
        return order

    def _truck(self, gross=50000.0, tare=15000.0):
        """A truck weighed and inspected, its receipt not yet prepared."""
        order = self._order()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'MD-0001', 'purchase_id': order.id, 'partner_id': order.partner_id.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(gross)
        ticket.record_second(tare)
        ticket.with_user(self.verifier).verify_blind(gross, tare)
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(self.line, 12.0)
        insp.action_submit_results()
        insp.with_user(self.labmgr).decide('accept')
        return visit, insp

    def test_one_truck_two_orders_each_with_its_own_receipt(self):
        visit, insp = self._truck()
        self.assertEqual(insp.payable_qty, 35000.0)
        extra = self._extra_order()
        share = self.env['mos.gate.visit.share'].with_user(self.guard).create({'visit_id': visit.id, 'purchase_id': extra.id, 'product_id': self.additive.id})
        self.env['mos.gate.weigh.step'].with_user(self.guard).create({'visit_id': visit.id, 'sequence': 10, 'weight_kg': 50000.0, 'note': 'loaded'})
        self.env['mos.gate.weigh.step'].with_user(self.guard).create({'visit_id': visit.id, 'sequence': 20, 'weight_kg': 48000.0, 'note': 'after the improver came off'})
        visit.with_user(self.guard).action_allocate_from_steps()
        self.assertEqual(share.allocated_kg, 2000.0)
        self.assertEqual((visit.shares_kg, visit.primary_kg), (2000.0, 33000.0))
        insp.invalidate_recordset()
        self.assertEqual(insp.payable_qty, 33000.0, 'what the other order took is not paid for as wheat')
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.guard).action_prepare_share_grns()
        self.assertEqual(share.grn_state, 'prepared')
        visit.with_user(self.store).action_confirm_grn()
        self.assertTrue(any('not confirmed' in p for p in visit._exit_blockers()), 'the other receipt is still open')
        with self.assertRaises(UserError):
            share.with_user(self.buyer).action_confirm()  # the buyer of that order cannot confirm its receipt
        visit.with_user(self.store).action_confirm_share_grns()
        self.assertEqual(share.grn_state, 'confirmed')
        self.assertFalse(any('not confirmed' in p and extra.name in p for p in visit._exit_blockers()))
        stock = self.env['stock.quant'].sudo().search([('product_id', '=', self.additive.id), ('location_id.usage', '=', 'internal')])
        self.assertEqual(sum(stock.mapped('quantity')), 2000.0)
        with self.assertRaises(UserError):
            share.write({'allocated_kg': 5.0})  # frozen once prepared
        with self.assertRaises(UserError):
            share.unlink()

    def test_allocation_cannot_exceed_the_ticket(self):
        visit, _insp = self._truck()
        extra = self._extra_order(qty=40000.0)
        share = self.env['mos.gate.visit.share'].with_user(self.guard).create({'visit_id': visit.id, 'purchase_id': extra.id, 'product_id': self.additive.id, 'allocated_kg': 40000.0})
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_prepare_share_grns()  # 40,000 against a 35,000 net
        self.assertEqual(share.grn_state, 'none')
        share.allocated_kg = 0.0
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_prepare_share_grns()  # no weight given

    def test_weighings_must_match_the_documents(self):
        visit, _insp = self._truck()
        extra = self._extra_order()
        self.env['mos.gate.visit.share'].create({'visit_id': visit.id, 'purchase_id': extra.id, 'product_id': self.additive.id})
        self.env['mos.gate.weigh.step'].create({'visit_id': visit.id, 'sequence': 10, 'weight_kg': 50000.0})
        with self.assertRaises(UserError):
            visit.action_allocate_from_steps()  # one weighing for one extra document is too few
        self.env['mos.gate.weigh.step'].create({'visit_id': visit.id, 'sequence': 20, 'weight_kg': 52000.0})
        with self.assertRaises(UserError):
            visit.action_allocate_from_steps()  # the truck got heavier
        with self.assertRaises(ValidationError):
            self.env['mos.gate.weigh.step'].create({'visit_id': visit.id, 'weight_kg': 0})

    def test_the_visits_own_order_is_not_a_second_document(self):
        visit, _insp = self._truck()
        with self.assertRaises(ValidationError):
            self.env['mos.gate.visit.share'].create({'visit_id': visit.id, 'purchase_id': visit.purchase_id.id, 'product_id': self.wheat.id})
        with self.assertRaises(ValidationError):
            self.env['mos.gate.visit.share'].create({'visit_id': visit.id, 'purchase_id': self._extra_order().id, 'product_id': self.wheat.id})  # that order has no wheat
