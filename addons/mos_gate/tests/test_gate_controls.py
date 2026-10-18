from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestGateControls(TransactionCase):
    """Watch list, shifts, exit pass, vehicles, incidents, material passes and the throughput report."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLG', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('gguard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('gverifier', 'mos_gate.group_mos_gate_verifier')
        cls.approver = user('gapprover', 'mos_gate.group_mos_gate_pass_approver')
        cls.clerk = user('gclerk', 'mos_core.group_mos_user')

    def _visit(self, plate='GC-1', purpose='inbound_po', **extra):
        return self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purpose': purpose, **extra})

    def _clear(self, visit):
        # Other modules add exit rules (lab decision, delivery dispatch); these tests are about the gate itself.
        with patch.object(self.registry['mos.gate.visit'], '_exit_blockers', lambda rec: []):
            visit.action_clear_exit()

    def _weigh(self, visit, first, second):
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(first)
        ticket.record_second(second)
        ticket.with_user(self.verifier).verify_blind(first, second)
        return ticket

    # -- watch list ---------------------------------------------------------------------------------
    def test_blacklist_blocks_and_warns(self):
        self.env['mos.gate.blacklist'].create({'kind': 'plate', 'value': ' bad-1 ', 'severity': 'block', 'reason': 'Tampered seals'})
        self.env['mos.gate.blacklist'].create({'kind': 'driver', 'value': 'Abebe', 'severity': 'warn', 'reason': 'Late twice'})
        with self.assertRaises(UserError):
            self._visit('BAD-1')
        visit = self._visit('OK-1', driver_name='abebe')
        self.assertIn('Late twice', visit.blacklist_warning)

    def test_blacklist_expiry_and_deactivation(self):
        old = self.env['mos.gate.blacklist'].create({'kind': 'plate', 'value': 'EXP-1', 'reason': 'x', 'expires_on': fields.Date.today() - timedelta(days=1)})
        self.assertTrue(self._visit('EXP-1'))
        old.unlink()
        gone = self.env['mos.gate.blacklist'].create({'kind': 'transporter', 'value': 'Slow Trans', 'reason': 'x', 'active': False})
        self.assertTrue(self._visit('EXP-2', transporter='Slow Trans'))
        self.assertTrue(gone)

    # -- shifts ----------------------------------------------------------------------------------------
    def test_shift_required_when_policy_is_on(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_gate.require_shift', 'True')
        with self.assertRaises(UserError):
            self._visit('SH-1')
        shift = self.env['mos.gate.shift'].with_user(self.guard).create({'site_id': self.site.id})
        visit = self._visit('SH-1')
        self.assertEqual(visit.shift_id, shift)
        with self.assertRaises(ValidationError):
            self.env['mos.gate.shift'].with_user(self.guard).create({'site_id': self.site.id})
        with self.assertRaises(UserError):
            shift.with_user(self.guard).action_close()  # a vehicle is still on site, no handover notes
        shift.handover_notes = 'One truck waiting'
        shift.with_user(self.guard).action_close()
        self.assertEqual(shift.state, 'closed')
        with self.assertRaises(UserError):
            self._visit('SH-2')  # shift closed again

    def test_admin_ignores_shift_policy(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_gate.require_shift', 'True')
        self.assertTrue(self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'ADM-1'}))

    # -- exit pass ---------------------------------------------------------------------------------------
    def test_exit_pass_code_and_scan(self):
        visit = self._visit('EX-1')
        self._weigh(visit, 40000.0, 14000.0)
        self._clear(visit)
        self.assertTrue(visit.exit_code)
        self.assertTrue(visit.exit_qr, 'the QR image is generated from the code')
        with self.assertRaises(UserError):
            visit.action_exit(code='WRONG')
        done = self.env['mos.gate.visit'].with_user(self.guard).scan_exit(visit.exit_code.lower())
        self.assertEqual(done, visit)
        self.assertEqual(visit.state, 'exited')
        with self.assertRaises(UserError):
            self.env['mos.gate.visit'].scan_exit(visit.exit_code)  # already out

    def test_exit_code_enforced_by_policy(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_gate.require_exit_code', 'True')
        visit = self._visit('EX-2')
        self._weigh(visit, 40000.0, 14000.0)
        self._clear(visit)
        with self.assertRaises(UserError):
            visit.action_exit()
        visit.action_exit(code=visit.exit_code)
        self.assertEqual(visit.state, 'exited')

    # -- stored tare, overload, deviation ---------------------------------------------------------------
    def test_tare_is_stored_after_posting_and_reused_outbound(self):
        visit = self._visit('TR-1')
        self._weigh(visit, 40000.0, 14000.0)
        vehicle = self.env['mos.gate.vehicle'].for_plate('TR-1')
        self.assertEqual(vehicle.stored_tare, 14000.0)
        self.assertTrue(vehicle.tare_valid)
        self._clear(visit)
        visit.action_exit()

        out = self._visit('TR-1', purpose='outbound_do')
        out.action_start_weighing()
        ticket = out.ticket_id
        ticket.use_stored_tare()
        self.assertEqual(ticket.first_weight, 14000.0)
        self.assertEqual(ticket.tare_source, 'stored')
        ticket.record_second(38000.0)
        self.assertEqual(ticket.net_weight, 24000.0)
        ticket.with_user(self.verifier).verify_blind(14000.0, 38000.0)
        self.assertEqual(self.env['mos.gate.vehicle'].for_plate('TR-1').stored_tare, 14000.0, 'a stored tare is not re-stored')

    def test_stored_tare_refused_when_missing_or_stale(self):
        out = self._visit('TR-2', purpose='outbound_do')
        out.action_start_weighing()
        with self.assertRaises(UserError):
            out.ticket_id.use_stored_tare()  # unknown vehicle
        vehicle = self.env['mos.gate.vehicle'].create({'plate': 'tr-2', 'stored_tare': 12000.0})
        vehicle.tare_at = fields.Datetime.now() - timedelta(days=90)
        with self.assertRaises(UserError):
            out.ticket_id.use_stored_tare()  # older than 30 days
        inbound = self._visit('TR-3')
        inbound.action_start_weighing()
        with self.assertRaises(UserError):
            inbound.ticket_id.use_stored_tare()

    def test_overload_and_tare_deviation_open_incidents(self):
        self.env['mos.gate.vehicle'].create({'plate': 'OV-1', 'stored_tare': 14000.0, 'max_gross_kg': 45000.0})
        visit = self._visit('OV-1')
        ticket = self._weigh(visit, 52000.0, 15500.0)
        self.assertTrue(ticket.overloaded)
        self.assertEqual(ticket.tare_deviation, 1500.0)
        kinds = sorted(visit.incident_ids.mapped('kind'))
        self.assertEqual(kinds, ['overload', 'tare_deviation'])
        incident = visit.incident_ids.filtered(lambda i: i.kind == 'overload')
        with self.assertRaises(UserError):
            incident.action_close()
        incident.action_investigate()
        incident.resolution = 'Driver unloaded 7 t'
        incident.action_close()
        self.assertEqual(incident.state, 'closed')
        self.assertRegex(incident.name, r'^INC-\d{4}-\d{5}$')

    # -- material gate passes ----------------------------------------------------------------------------
    def _pass(self, kind='returnable', **extra):
        vals = {
            'site_id': self.site.id,
            'kind': kind,
            'carrier': 'Workshop van',
            'purpose': 'Repair',
            'expected_return': fields.Date.today() + timedelta(days=3),
            'line_ids': [Command.create({'name': 'Gearbox', 'quantity': 1})],
        }
        vals.update(extra)
        return self.env['mos.gate.pass'].with_user(self.clerk).create(vals)

    def test_material_pass_flow_with_separate_approver(self):
        gp = self._pass()
        with self.assertRaises(UserError):
            gp.with_user(self.clerk).action_approve()  # not an approver
        gp.with_user(self.approver).action_approve()
        self.assertEqual(gp.approved_by_id, self.approver)
        gp.with_user(self.guard).action_out()
        self.assertEqual(gp.state, 'out')
        with self.assertRaises(UserError):
            gp.action_close()  # returnable goods are still out
        gp.with_user(self.guard).action_return()
        gp.action_close()
        self.assertEqual(gp.state, 'closed')
        self.assertRegex(gp.name, r'^GP-\d{4}-\d{5}$')

    def test_requester_cannot_approve_own_pass(self):
        gp = self.env['mos.gate.pass'].with_user(self.approver).create(
            {'site_id': self.site.id, 'kind': 'final', 'carrier': 'x', 'purpose': 'Scrap sale', 'line_ids': [Command.create({'name': 'Scrap metal', 'quantity': 5})]}
        )
        with self.assertRaises(UserError):
            gp.action_approve()

    def test_material_pass_validation_and_overdue(self):
        with self.assertRaises(ValidationError):
            self._pass(expected_return=False)
        with self.assertRaises(ValidationError):
            self._pass(line_ids=[Command.create({'name': 'Bad', 'quantity': 0})])
        empty = self._pass(line_ids=[])
        with self.assertRaises(UserError):
            empty.with_user(self.approver).action_approve()
        late = self._pass(expected_return=fields.Date.today() - timedelta(days=1))
        late.with_user(self.approver).action_approve()
        late.with_user(self.guard).action_out()
        self.assertTrue(late.overdue)
        self.assertIn(late, self.env['mos.gate.pass'].search([('overdue', '=', True)]))
        self.assertNotIn(late, self.env['mos.gate.pass'].search([('overdue', '=', False)]))

    # -- report ------------------------------------------------------------------------------------------
    def test_throughput_report(self):
        visit = self._visit('RP-1')
        self._weigh(visit, 40000.0, 14000.0)
        self._clear(visit)
        visit.action_exit()
        self.env.flush_all()
        rows = self.env['mos.gate.report'].search([('visit_id', '=', visit.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.net_weight, 26000.0)
        self.assertEqual(rows.visits, 1)
        self.assertGreaterEqual(rows.dwell_minutes, 0.0)
