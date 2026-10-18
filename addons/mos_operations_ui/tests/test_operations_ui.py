import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.mos_flows.models.action_wizard import ALLOWED
from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


@tagged('post_install', '-at_install')
class TestOperationsUi(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        cls.fin = new_test_user(cls.env, login='xuifin', groups='mos_approvals.group_mos_finance_manager')
        cls.fin.mos_site_ids = [Command.set(cls.site.ids)]
        cls.inv = new_test_user(cls.env, login='xuiinv', groups='mos_inventory.group_mos_inventory_manager')
        cls.inv.mos_site_ids = [Command.set(cls.site.ids)]

    # -- helpers: do what the browser does ------------------------------------------------------------------------------
    def _open(self, record, user, method=None, title=None, button='action_ui'):
        """Press a button: the action it returns, opened as the wizard the screen would show."""
        record = record.with_user(user)
        if method:
            record = record.with_context(mos_method=method, mos_title=title or method)
        action = getattr(record, button)()
        self.assertEqual(action['res_model'] in ('mos.action.wizard', 'mos.weigh.wizard'), True)
        return self.env[action['res_model']].with_user(user).with_context(**action['context'])

    def _generic(self, record, user, method, **answers):
        wizard = self._open(record, user, method)
        form = wizard.create(answers)
        form.action_confirm()
        return form

    # -- weighing -------------------------------------------------------------------------------------------------------
    def _visit(self, plate='UI-0001'):
        order = self._order()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': order.partner_id.id})
        visit.action_start_weighing()
        return visit

    def test_a_guard_weighs_a_truck_and_a_second_person_verifies_it_blind(self):
        visit = self._visit()
        self.assertEqual(visit.ticket_stage, 'first')
        action = visit.with_user(self.guard).action_weigh_ui()
        self.assertEqual((action['context']['default_step'], action['context']['default_instruction']), ('first', 'Weigh the loaded truck (gross).'))
        self.env['mos.weigh.wizard'].with_user(self.guard).with_context(**action['context']).create({'weight': 50000.0}).action_confirm()
        self.assertEqual((visit.ticket_stage, visit.ticket_id.first_weight), ('second', 50000.0))
        second = visit.with_user(self.guard).action_weigh_ui()
        self.assertEqual(second['context']['default_instruction'], 'Weigh the empty truck (tare).')
        self.env['mos.weigh.wizard'].with_user(self.guard).with_context(**second['context']).create({'weight': 15000.0}).action_confirm()
        self.assertEqual((visit.ticket_stage, visit.ticket_id.state), ('verify', 'weighed'))
        blind = visit.with_user(self.verifier).action_weigh_ui()
        self.assertEqual(blind['context']['default_step'], 'verify')
        wizard = self.env['mos.weigh.wizard'].with_user(self.verifier).with_context(**blind['context'])
        with self.assertRaises(UserError):
            wizard.create({'weight_gross': 50000.0, 'weight_tare': 14000.0}).action_confirm()  # the verifier read a different tare
        self.assertEqual(visit.ticket_stage, 'verify', 'a wrong reading does not post the ticket')
        wizard.create({'weight_gross': 50000.0, 'weight_tare': 15000.0}).action_confirm()
        self.assertEqual((visit.ticket_stage, visit.ticket_id.state, visit.net_weight), ('posted', 'posted', 35000.0))
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_weigh_ui()  # nothing more to weigh

    def test_a_weight_that_makes_no_sense_is_refused_by_the_ticket(self):
        visit = self._visit('UI-0002')
        action = visit.with_user(self.guard).action_weigh_ui()
        wizard = self.env['mos.weigh.wizard'].with_user(self.guard).with_context(**action['context'])
        with self.assertRaises(UserError):
            wizard.create({'weight': 0.0}).action_confirm()
        wizard.create({'weight': 40000.0}).action_confirm()
        second = visit.with_user(self.guard).action_weigh_ui()
        with self.assertRaises(UserError):
            self.env['mos.weigh.wizard'].with_user(self.guard).with_context(**second['context']).create({'weight': 45000.0}).action_confirm()  # a tare above the gross

    def test_a_visit_without_a_ticket_has_nothing_to_weigh(self):
        order = self._order()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'UI-0003', 'purchase_id': order.id, 'partner_id': order.partner_id.id})
        self.assertEqual(visit.ticket_stage, 'none')
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_weigh_ui()

    def test_a_posted_ticket_is_replaced_by_a_reweigh_with_a_reason_and_can_carry_a_photo(self):
        visit = self._visit('UI-0004')
        ticket = visit.ticket_id
        ticket.with_user(self.guard).record_first(50000.0)
        ticket.with_user(self.guard).record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(50000.0, 15000.0)
        reason = self.env['mos.reason'].create({'domain': 'reweigh', 'code': 'UIRW', 'name': 'Scale drifted'})
        self._generic(ticket, self.guard, 'reweigh', reweigh_reason_id=reason.id)
        self.assertEqual(ticket.state, 'superseded')
        self.assertEqual(visit.ticket_id.supersedes_id, ticket)
        self.assertEqual(visit.ticket_stage, 'first', 'the new ticket waits for its own weighing')
        self._generic(visit.ticket_id, self.guard, 'action_attach_photo', filename='scale.pdf', file=base64.b64encode(PDF).decode())
        photo = self.env['mos.attachment'].search([('res_model', '=', 'mos.weigh.ticket'), ('res_id', '=', visit.ticket_id.id)])
        self.assertEqual((photo.name, photo.note), ('scale.pdf', 'Weighing photo'))

    # -- exceptions ------------------------------------------------------------------------------------------------------
    def test_an_exception_is_resolved_or_dismissed_from_a_dialog(self):
        exc = self.env['mos.exception']
        a = exc.create({'kind': 'gate_incident', 'summary': 'Seal broken', 'severity': 'medium'})
        self._generic(a, self.manager, 'action_resolve', reason='Seal replaced and photographed')
        self.assertEqual((a.state, a.resolution), ('resolved', 'Seal replaced and photographed'))
        b = exc.create({'kind': 'long_stay', 'summary': 'Truck 3 days', 'severity': 'medium'})
        self._generic(b, self.manager, 'action_dismiss', reason='Waiting for the buyer by agreement')
        self.assertEqual(b.state, 'dismissed')
        high = exc.create({'kind': 'gate_incident', 'summary': 'Theft', 'severity': 'high'})
        with self.assertRaises(UserError):
            self._generic(high, self.manager, 'action_dismiss', reason='no')  # only an administrator may dismiss a high one
        with self.assertRaises(UserError):
            self._generic(exc.create({'kind': 'long_stay', 'summary': 'x'}), self.manager, 'action_resolve', reason='')  # say what was done

    # -- orders, contracts, lots, locks ---------------------------------------------------------------------------------
    def test_an_approved_request_becomes_an_order_from_a_dialog(self):
        request = self.env['mos.purchase.request'].with_user(self.requester).create(
            {'site_id': self.site.id, 'reason': 'Stock is low', 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': 20000.0, 'estimated_price': 20.0})]}
        )
        request.with_user(self.requester).action_submit()
        self.assertEqual(request.state, 'approved')
        self._generic(request, self.buyer, 'action_convert', partner_id=self.s1.id)
        self.assertEqual(request.state, 'converted')
        order = request.sudo().order_id
        self.assertEqual((order.partner_id, order.order_line.mapped('product_qty')), (self.s1, [20000.0]))
        with self.assertRaises(UserError):
            self._generic(request, self.buyer, 'action_convert', partner_id=self.s1.id)  # already ordered

    def test_a_call_off_and_a_close_are_made_from_the_contract(self):
        contract = self.env['mos.purchase.contract'].with_user(self.buyer).create(
            {'supplier_id': self.s1.id, 'product_id': self.wheat.id, 'qty': 50000.0, 'price': 20.0, 'valid_from': fields.Date.today(), 'valid_to': fields.Date.today() + timedelta(days=30)}
        )
        contract.with_user(self.manager).action_activate()
        self._generic(contract, self.buyer, 'action_new_calloff', number=12000.0)
        self.assertEqual(contract.order_ids.order_line.product_qty, 12000.0)
        self._generic(contract, self.manager, 'action_close', reason='Season over')
        self.assertEqual((contract.state, contract.closed_note), ('closed', 'Season over'))

    def test_a_lot_is_split_from_its_form(self):
        kg = self.env.ref('uom.product_uom_kgm')
        product = self.env['product.product'].create({'name': 'UI lot wheat', 'type': 'consu', 'is_storable': True, 'tracking': 'lot', 'uom_id': kg.id})
        lot = self.env['stock.lot'].create({'product_id': product.id})
        location = self.site.warehouse_id.lot_stock_id
        self.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id, 'location_id': location.id, 'inventory_quantity': 900.0, 'lot_id': lot.id}).action_apply_inventory()
        self._generic(lot.with_user(self.store), self.store, 'action_split', number=250.0, text='UI-PART')
        part = self.env['stock.lot'].search([('name', '=', 'UI-PART')])
        self.assertEqual(part.mos_parent_lot_id, lot)

    def test_a_soft_close_and_a_conversion_version_are_set_from_dialogs(self):
        lock = self.env['mos.period.lock'].sudo().search([('area', '=', 'payables')], limit=1) or self.env['mos.period.lock'].sudo().create({'area': 'payables'})
        yesterday = fields.Date.today() - timedelta(days=1)
        self._generic(lock, self.fin, 'set_soft_lock', date=yesterday, reason='Month-end review')
        self.assertEqual(lock.soft_lock_date, yesterday)
        self._generic(lock, self.fin, 'set_soft_lock', reason='Reopened')  # an empty date reopens
        self.assertFalse(lock.soft_lock_date)
        conversion = self.env['mos.item.conversion'].create({'product_id': self.wheat.id, 'unit_name': 'bag', 'factor': 50.0, 'effective_from': '2026-01-01'})
        self._generic(conversion, self.inv, 'action_new_version', number=48.5, date=fields.Date.today())
        self.assertEqual(conversion.search([('product_id', '=', self.wheat.id), ('unit_name', '=', 'bag')], order='effective_from desc', limit=1).factor, 48.5)
        with self.assertRaises(UserError):
            self._generic(conversion, self.inv, 'action_new_version', number=47.0)  # no date

    # -- nothing is offered that cannot be done -------------------------------------------------------------------------
    def test_every_dialog_the_screens_offer_points_at_a_real_method(self):
        for (model, method), needs in ALLOWED.items():
            self.assertIn(model, self.env, f'{model} is not a model')
            record = self.env[model]
            self.assertTrue(hasattr(record, method), f'{model}.{method} does not exist')
            self.assertTrue(hasattr(record, 'action_ui'), f'{model} cannot open the dialog')
            for kind in needs:
                self.assertIn(kind, ('reason', 'number', 'order', 'basis', 'text', 'date', 'supplier', 'decision', 'filename', 'file', 'reweigh_reason'), f'unknown kind {kind}')
