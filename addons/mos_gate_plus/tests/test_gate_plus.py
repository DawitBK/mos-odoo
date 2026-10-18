from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestGatePlus(TransactionCase):
    """Posts and weighbridges, plate search, expected arrivals, tolerance, clearance log and override, service visits, paper mode."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLG2', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('gpguard', 'mos_gate.group_mos_gate_guard')
        cls.sup = user('gpsup', 'mos_flows.group_mos_gate_supervisor')
        cls.verifier = user('gpverifier', 'mos_gate.group_mos_gate_verifier')
        cls.buyer = user('gpbuyer', 'purchase.group_purchase_user,mos_core.group_mos_user')
        kg = env.ref('uom.product_uom_kgm')
        cls.wheat = env['product.product'].create({'name': 'Gate wheat', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 20.0})
        cls.supplier = env['res.partner'].create({'name': 'Gate Supplier', 'supplier_rank': 1})
        cls.post = env['mos.gate.post'].create({'code': 'G1', 'name': 'Main gate', 'site_id': cls.site.id, 'direction': 'both'})
        cls.bridge = env['mos.weighbridge'].create({'code': 'WB1', 'name': 'Bridge 1', 'post_id': cls.post.id, 'capacity_kg': 80000.0, 'division_kg': 10.0})

    def _order(self, qty=40000.0):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.wheat.id, 'product_qty': qty, 'price_unit': 20.0})]}
        )
        order.with_user(self.buyer).button_confirm()
        return order

    def _visit(self, plate, order=None, **extra):
        vals = {'site_id': self.site.id, 'plate': plate, **extra}
        if order:
            vals.update({'purchase_id': order.id, 'partner_id': order.partner_id.id})
        return self.env['mos.gate.visit'].with_user(self.guard).create(vals)

    def _weighed(self, visit, gross=50000.0, tare=15000.0):
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(gross)
        ticket.record_second(tare)
        ticket.with_user(self.verifier).verify_blind(gross, tare)
        return ticket

    # -- posts and weighbridges --------------------------------------------------------------------------------------
    def test_ticket_numbers_come_from_the_weighbridge_and_weights_must_fit_the_scale(self):
        visit = self._visit('GP-1', self._order(), gate_post_id=self.post.id, weighbridge_id=self.bridge.id)
        visit.action_start_weighing()
        ticket = visit.ticket_id
        self.assertRegex(ticket.name, r'^WT-WB1-\d{4}-\d{6}$')
        with self.assertRaises(UserError):
            ticket.record_first(90000.0)  # above the capacity
        with self.assertRaises(UserError):
            ticket.record_first(50005.0)  # not a multiple of 10 kg
        ticket.record_first(50000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(50000.0, 15000.0)
        second = self._visit('GP-2', self._order(), gate_post_id=self.post.id, weighbridge_id=self.bridge.id)
        second.action_start_weighing()
        self.assertEqual(int(second.ticket_id.name[-6:]), int(ticket.name[-6:]) + 1, 'gap-free per weighbridge')
        with self.assertRaises(ValidationError):
            self.env['mos.weighbridge'].create({'code': 'WB2', 'name': 'Bad', 'post_id': self.post.id, 'capacity_kg': 0})

    def test_a_gate_accepts_only_its_kind_of_visit(self):
        exit_gate = self.env['mos.gate.post'].create({'code': 'G2', 'name': 'Exit gate', 'site_id': self.site.id, 'direction': 'out', 'allows': 'goods'})
        with self.assertRaises(UserError):
            self._visit('GP-3', gate_post_id=exit_gate.id, purpose='inbound_po')
        with self.assertRaises(UserError):
            self._visit('GP-4', gate_post_id=exit_gate.id, purpose='service')
        service_gate = self.env['mos.gate.post'].create({'code': 'G3', 'name': 'Contractor gate', 'site_id': self.site.id, 'allows': 'service'})
        with self.assertRaises(UserError):
            self._visit('GP-5', gate_post_id=service_gate.id, purpose='inbound_po')
        self.assertTrue(self._visit('GP-6', gate_post_id=service_gate.id, purpose='service'))

    # -- plate search and expected arrivals -------------------------------------------------------------------------
    def test_arrival_by_plate_finds_the_expected_visit_or_opens_a_walk_in(self):
        order = self._order()
        expected = self._visit('ab-1234', order, state='expected')
        found = self.env['mos.gate.visit'].with_user(self.guard).arrive_by_plate(' AB-1234 ', self.site)
        self.assertEqual(found, expected)
        self.assertEqual(found.state, 'arrived')
        walk_in = self.env['mos.gate.visit'].with_user(self.guard).arrive_by_plate('zz-9', self.site, driver_name='Kebede')
        self.assertEqual((walk_in.state, walk_in.plate, walk_in.driver_name), ('arrived', 'ZZ-9', 'Kebede'))
        with self.assertRaises(UserError):
            self.env['mos.gate.visit'].arrive_by_plate('', self.site)

    def test_cron_creates_expected_visits_for_orders_due_soon(self):
        due = self._order()
        due.order_line.date_planned = fields.Datetime.now() + timedelta(hours=6)
        later = self._order()
        later.order_line.date_planned = fields.Datetime.now() + timedelta(days=9)
        created = self.env['mos.gate.visit']._cron_expected_arrivals()
        self.assertGreaterEqual(created, 1)
        visit = self.env['mos.gate.visit'].search([('purchase_id', '=', due.id)])
        self.assertEqual((visit.state, visit.expected_auto, visit.plate), ('expected', True, f'TBA-{due.name}'))
        self.assertFalse(self.env['mos.gate.visit'].search([('purchase_id', '=', later.id)]))
        self.assertEqual(self.env['mos.gate.visit']._cron_expected_arrivals(), 0, 'no second visit for the same order')
        real = self.env['mos.gate.visit'].arrive_by_plate(due.name, self.site)
        self.assertEqual(real, visit, 'the guard can find it by the order number')
        self.assertEqual(real.state, 'arrived')

    # -- tolerance ---------------------------------------------------------------------------------------------------
    def test_weight_beyond_the_tolerance_opens_an_incident_and_can_block_the_exit(self):
        self.env['mos.gate.tolerance'].create({'purpose': 'inbound_po', 'pct': 5.0, 'action': 'warn'})
        self.env['mos.gate.tolerance'].create({'purpose': 'inbound_po', 'product_id': self.wheat.id, 'pct': 2.0, 'action': 'block'})
        order = self._order(qty=30000.0)
        visit = self._visit('GP-7', order)
        ticket = self._weighed(visit)  # 35 t on a 30 t order: 16.7 % over
        row = self.env['mos.gate.tolerance'].best_for(visit, self.wheat)
        self.assertEqual((row.pct, row.action), (2.0, 'block'), 'the product row is more specific than the general one')
        self.assertIn('16.7', ticket.tolerance_note)
        self.assertTrue(visit.tolerance_blocked)
        self.assertTrue(visit.incident_ids)
        self.assertTrue(any('tolerance' in p for p in visit._exit_blockers()))
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_approve_tolerance('ok')
        visit.with_user(self.sup).action_approve_tolerance('Supplier delivered next week\'s lot too')
        self.assertFalse(visit.tolerance_blocked)

    def test_weight_inside_the_tolerance_is_quiet(self):
        self.env['mos.gate.tolerance'].create({'pct': 5.0})
        visit = self._visit('GP-8', self._order(qty=35500.0))
        ticket = self._weighed(visit)
        self.assertFalse(ticket.tolerance_note)
        self.assertFalse(visit.tolerance_blocked)

    # -- clearance log and override -----------------------------------------------------------------------------------
    def test_every_clearance_attempt_is_logged_and_a_seal_can_be_overridden_by_a_supervisor(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_gate_plus.require_seal', 'True')
        visit = self._visit('GP-9', self._order(), seal_number='S-100')
        self._weighed(visit)
        visit.seal_intact = False
        with patch.object(type(visit), '_log_clearance', autospec=True) as logged:
            with self.assertRaises(UserError):
                visit.with_user(self.guard).action_clear_exit()
        self.assertEqual(logged.call_args.args[1], 'refused')
        self.assertIn('seal', logged.call_args.args[2])
        visit._log_clearance('refused', 'the seal is broken or missing')
        refused = visit.clearance_ids
        self.assertEqual(refused.result, 'refused')
        self.assertIn('seal', refused.reasons)
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_override_clearance('please')  # a guard cannot
        with self.assertRaises(UserError):
            visit.with_user(self.sup).action_override_clearance('')
        visit.with_user(self.sup).action_override_clearance('Seal replaced by the supplier in front of the guard')
        self.assertTrue(visit.clearance_override_by_id)
        self.assertFalse(any('seal' in p for p in visit._exit_blockers()))
        with self.assertRaises(UserError):
            refused.unlink()
        with self.assertRaises(UserError):
            refused.write({'reasons': 'edited'})

    # -- service visits ------------------------------------------------------------------------------------------------
    def test_service_visit_leaves_only_with_its_tools(self):
        visit = self._visit('GP-10', purpose='service', persons_count=3, work_permit_ref='WP-77')
        visit.write({'tool_ids': [Command.create({'name': 'Welding set', 'qty_in': 1}), Command.create({'name': 'Wrench box', 'qty_in': 2})]})
        visit.state = 'weighed'
        self.assertTrue(any('tools not taken out' in p for p in visit._exit_blockers()))
        welding, wrench = visit.tool_ids
        welding.qty_out = 1
        wrench.qty_out = 1
        self.assertTrue(any('Wrench box' in p for p in visit._exit_blockers()))
        wrench.explanation = 'One box left with the maintenance team by agreement'
        self.assertFalse(any('tools' in p for p in visit._exit_blockers()))
        with self.assertRaises(ValidationError):
            welding.qty_out = 5

    # -- paper mode ------------------------------------------------------------------------------------------------------
    def test_paper_book_back_entry_creates_dated_visits_and_accounts_for_every_slip(self):
        book = self.env['mos.paper.book'].with_user(self.sup).create({'site_id': self.site.id, 'prefix': 'PS-', 'first_no': 1, 'last_no': 20})
        self.assertEqual(len(book.slip_ids), 20)
        with self.assertRaises(ValidationError):
            self.env['mos.paper.book'].create({'site_id': self.site.id, 'prefix': 'PS-', 'first_no': 10, 'last_no': 30})  # overlaps
        slip = book.slip_ids[0]
        arrived = fields.Datetime.now() - timedelta(hours=9)
        slip.write({'plate': 'pp-1', 'driver_name': 'Almaz', 'arrived_at': arrived, 'exited_at': arrived + timedelta(hours=1), 'gross_kg': 42000.0, 'tare_kg': 14000.0})
        slip.with_user(self.guard).action_enter()
        visit = slip.visit_id
        self.assertEqual((visit.plate, visit.state, visit.arrived_at, visit.from_paper), ('PP-1', 'exited', arrived, True))
        self.assertEqual(visit.ticket_id.net_weight, 28000.0)
        self.assertTrue(visit.ticket_id.provisional)
        self.assertEqual((slip.state, book.used_count, book.open_count), ('entered', 1, 19))
        with self.assertRaises(UserError):
            slip.action_enter()
        empty = book.slip_ids[1]
        with self.assertRaises(UserError):
            empty.action_enter()  # nothing written on it
        with self.assertRaises(UserError):
            empty.with_user(self.sup).action_void('')
        empty.with_user(self.sup).action_void('Torn in the rain')
        self.assertEqual(empty.state, 'void')
        late = book.slip_ids[2]
        late.write({'plate': 'pp-2', 'arrived_at': arrived, 'exited_at': arrived - timedelta(hours=1)})
        with self.assertRaises(UserError):
            late.action_enter()  # left before it arrived

    # -- weight basis and the register ---------------------------------------------------------------------------------
    def test_the_supplier_decides_which_weight_is_paid(self):
        self.supplier.mos_weight_basis = 'lower'
        visit = self._visit('GP-11', self._order(), declared_qty=34000.0)
        self.assertEqual(visit.weight_basis, 'lower')
        self._weighed(visit)  # the bridge says 35 000
        insp = self.env['mos.lab.inspection'].sudo().create({'visit_id': visit.id, 'product_id': self.wheat.id, 'spec_id': self._spec().id})
        self.assertEqual(insp.payable_qty, 34000.0, 'the lower of bridge and declared')
        visit.declared_qty = 36000.0
        insp.invalidate_recordset()
        self.assertEqual(insp.payable_qty, 35000.0)

    def _spec(self):
        spec = self.env['mos.lab.spec'].create({'product_id': self.wheat.id, 'line_ids': [Command.create({'name': 'Moisture', 'min_value': 0, 'max_value': 14})]})
        spec.action_approve()
        return spec

    def test_daily_register_lists_visits_and_tickets_including_superseded(self):
        order = self._order()
        visit = self._visit('GP-12', order)
        ticket = self._weighed(visit)
        reason = self.env['mos.reason'].create({'domain': 'reweigh', 'code': 'RWX', 'name': 'Scale drift'})
        new = ticket.with_user(self.guard).reweigh(reason)
        register = self.env['mos.gate.register'].create({'site_id': self.site.id, 'date': fields.Date.today()})
        self.assertIn(visit, register.visit_ids)
        self.assertEqual(register.superseded, 1)
        self.assertIn(ticket, register.ticket_ids)
        self.assertIn(new, register.ticket_ids)
        self.assertEqual(register.net_total, 0.0, 'the superseded ticket does not count and the new one is not posted yet')
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_gate_plus.report_gate_register', register.ids)
        self.assertIn(b'GP-12', html)
