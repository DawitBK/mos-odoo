from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestInboundFlow(TransactionCase):
    """Flow A end to end: gate, weighbridge, lab, system-prepared receipt, storekeeper, exit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILL', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('guard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('verifier', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('analyst', 'mos_lab.group_mos_lab_analyst')
        cls.manager = user('labmgr', 'mos_lab.group_mos_lab_manager')
        cls.store = user('store', 'mos_inbound.group_mos_storekeeper')
        cls.buyer = user('buyer', 'purchase.group_purchase_user,mos_core.group_mos_user')

        cls.supplier = env['res.partner'].create({'name': 'Wheat Farmers Union', 'supplier_rank': 1})
        cls.product = env['product.product'].create(
            {
                'name': 'Durum wheat',
                'type': 'consu',
                'is_storable': True,
                'uom_id': env.ref('uom.product_uom_kgm').id,
                'standard_price': 20.0,
            }
        )
        cls.po = env['purchase.order'].with_user(cls.buyer).create(
            {
                'partner_id': cls.supplier.id,
                'order_line': [Command.create({'product_id': cls.product.id, 'product_qty': 40000, 'price_unit': 20.0})],
            }
        )
        cls.po.with_user(cls.buyer).button_confirm()

        cls.spec = env['mos.lab.spec'].create(
            {
                'product_id': cls.product.id,
                'line_ids': [
                    Command.create({'name': 'Moisture %', 'min_value': 0, 'max_value': 13.5}),
                    Command.create({'name': 'Hectolitre weight kg/hl', 'min_value': 76, 'max_value': 90}),
                ],
            }
        )
        cls.spec.action_approve()
        cls.reweigh_reason = env['mos.reason'].create({'domain': 'reweigh', 'code': 'RW1', 'name': 'Scale drift'})

    # -- helpers ----------------------------------------------------------------------------------
    def _visit_weighed(self, gross=50000.0, tare=15000.0):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create(
            {'site_id': self.site.id, 'plate': ' aa-1234 ', 'purchase_id': self.po.id, 'partner_id': self.supplier.id}
        )
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(gross)
        ticket.record_second(tare)
        ticket.with_user(self.verifier).verify_blind(gross, tare)
        return visit

    def _inspect(self, visit, moisture=12.0, hl=80.0):
        insp = visit.with_user(self.analyst).action_open_inspection()
        insp = insp.with_user(self.analyst)
        for line, value in zip(self.spec.line_ids, (moisture, hl)):
            insp.record_result(line, value)
        insp.action_submit_results()
        return insp

    # -- the happy path ---------------------------------------------------------------------------
    def test_full_inbound_flow(self):
        visit = self._visit_weighed()
        self.assertEqual(visit.plate, 'AA-1234')
        self.assertEqual(visit.net_weight, 35000.0)
        self.assertEqual(visit.state, 'weighed')
        self.assertRegex(visit.ticket_id.name, r'^WT-\d{4}-\d{6}$')

        insp = self._inspect(visit)
        self.assertEqual(insp.suggested_outcome, 'pass')
        insp.with_user(self.manager).decide('accept_deduction', 2.0, 'Slight foreign matter')
        self.assertEqual(insp.payable_qty, 34300.0)

        visit = visit.with_user(self.guard)
        picking = visit.action_prepare_grn()
        self.assertEqual(visit.grn_state, 'prepared')
        self.assertEqual(picking.move_ids.quantity, 34300.0)
        self.assertEqual(visit.action_prepare_grn(), picking, 'preparing twice must not create a second receipt')

        with self.assertRaises(UserError):
            visit.action_clear_exit()  # the receipt is not confirmed yet

        visit.with_user(self.store).action_confirm_grn()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(picking.move_ids.quantity, 34300.0)
        visit.action_clear_exit()
        visit.action_exit()
        self.assertEqual(visit.state, 'exited')

    def test_rejected_load_creates_no_receipt(self):
        visit = self._visit_weighed()
        insp = self._inspect(visit, moisture=16.0)
        self.assertEqual(insp.suggested_outcome, 'fail')
        insp.with_user(self.manager).decide('reject', note='Wet wheat')
        visit = visit.with_user(self.guard)
        self.assertFalse(visit.action_prepare_grn())
        self.assertFalse(visit.grn_picking_id)
        visit.action_clear_exit()
        self.assertEqual(visit.state, 'cleared')

    # -- weighing rules ---------------------------------------------------------------------------
    def test_weighing_rules(self):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'BB-1', 'purchase_id': self.po.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        with self.assertRaises(UserError):
            ticket.record_second(1000.0)  # second before first
        ticket.record_first(50000.0)
        with self.assertRaises(UserError):
            ticket.record_second(60000.0)  # tare above gross
        ticket.record_second(15000.0)
        with self.assertRaises(UserError):
            ticket.verify_blind(50000.0, 15000.0)  # the guard who weighed cannot verify
        with self.assertRaises(UserError):
            ticket.with_user(self.verifier).verify_blind(50000.0, 14990.0)  # mismatch
        ticket.with_user(self.verifier).verify_blind(50000.0, 15000.0)
        self.assertEqual(ticket.state, 'posted')

    def test_posted_ticket_is_immutable_and_reweigh_supersedes(self):
        visit = self._visit_weighed()
        ticket = visit.ticket_id
        with self.assertRaises(UserError):
            ticket.with_user(self.guard).write({'first_weight': 1.0})
        with self.assertRaises(UserError):
            ticket.unlink()
        with self.assertRaises(UserError):
            ticket.reweigh(self.env['mos.reason'].create({'domain': 'scrap', 'code': 'S1', 'name': 'wrong domain'}))
        new = ticket.reweigh(self.reweigh_reason)
        self.assertEqual(ticket.state, 'superseded')
        self.assertEqual(new.supersedes_id, ticket)
        self.assertEqual(visit.state, 'weighing')
        self.assertEqual(ticket.net_weight, 35000.0, 'the original is kept untouched')

    def test_ticket_numbers_have_no_gaps(self):
        numbers = []
        for plate in ('C1', 'C2', 'C3'):
            visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate})
            visit.action_start_weighing()
            numbers.append(int(visit.ticket_id.name.rsplit('-', 1)[1]))
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + 3)))

    def test_one_open_visit_per_vehicle(self):
        self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'DD-9'})
        with self.assertRaises(Exception):
            self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'dd-9'})

    # -- lab rules --------------------------------------------------------------------------------
    def test_decision_needs_a_different_person_and_failed_acceptance_needs_a_manager(self):
        visit = self._visit_weighed()
        insp = self._inspect(visit, moisture=15.0)
        with self.assertRaises(UserError):
            insp.with_user(self.analyst).decide('reject')  # same person who tested
        with self.assertRaises(UserError):
            insp.with_user(self.manager).decide('accept')  # failed result, no justification
        with self.assertRaises(UserError):
            insp.with_user(self.manager).decide('accept_deduction', 0.0)
        insp.with_user(self.manager).decide('accept', note='Customer agreed in writing')
        self.assertEqual(insp.state, 'decided')
        with self.assertRaises(UserError):
            insp.with_user(self.manager).write({'decision': 'reject'})

    def test_results_are_never_overwritten(self):
        visit = self._visit_weighed()
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        first = insp.record_result(self.spec.line_ids[0], 12.0)
        with self.assertRaises(UserError):
            first.write({'value': 5.0})
        with self.assertRaises(UserError):
            first.unlink()
        first.action_invalidate('Sample mix-up')
        second = insp.record_result(self.spec.line_ids[0], 13.0)
        insp.record_result(self.spec.line_ids[1], 80.0)
        self.assertEqual(insp._current_results()[self.spec.line_ids[0]], second)
        self.assertEqual(insp.suggested_outcome, 'pass')

    def test_approved_specification_cannot_change(self):
        with self.assertRaises(UserError):
            self.spec.line_ids[0].write({'max_value': 20.0})
        with self.assertRaises(UserError):
            self.spec.write({'product_id': self.product.id, 'version': 9})
        v2 = self.spec.action_new_version()
        self.assertEqual(v2.version, 2)
        v2.action_approve()
        self.assertEqual(self.spec.state, 'retired')
        self.assertEqual(self.env['mos.lab.spec'].in_force(self.product), v2)

    # -- scoping, segregation of duties, audit ----------------------------------------------------
    def test_user_sees_only_their_sites(self):
        other_site = self.env['mos.site'].create({'name': 'Depot', 'code': 'DEP'})
        hidden = self.env['mos.gate.visit'].create({'site_id': other_site.id, 'plate': 'ZZ-1'})
        seen = self.env['mos.gate.visit'].with_user(self.guard).search([])
        self.assertNotIn(hidden, seen)

    def test_buyer_cannot_confirm_the_receipt_of_their_own_order(self):
        visit = self._visit_weighed()
        insp = self._inspect(visit)
        insp.with_user(self.manager).decide('accept')
        visit.with_user(self.guard).action_prepare_grn()
        buyer_store = new_test_user(self.env, login='buyerstore', groups='mos_inbound.group_mos_storekeeper')
        buyer_store.mos_site_ids = [Command.set(self.site.ids)]
        self.env.cr.execute('UPDATE purchase_order SET create_uid = %s WHERE id = %s', [buyer_store.id, self.po.id])
        self.po.invalidate_recordset()
        with self.assertRaises(UserError):
            visit.with_user(buyer_store).action_confirm_grn()

    def test_audit_chain_detects_tampering(self):
        self._visit_weighed()
        audit = self.env['mos.audit.event']
        self.assertTrue(audit.search_count([]))
        self.assertIsNone(audit.verify_chain())
        with self.assertRaises(Exception), self.cr.savepoint():
            self.cr.execute("UPDATE mos_audit_event SET summary = 'x' WHERE id = (SELECT min(id) FROM mos_audit_event)")
        with self.assertRaises(UserError):
            with self.cr.savepoint():
                self.cr.execute('ALTER TABLE mos_audit_event DISABLE TRIGGER mos_audit_no_update')
                self.cr.execute("UPDATE mos_audit_event SET summary = 'forged' WHERE id = (SELECT min(id) FROM mos_audit_event)")
                self.assertIsNotNone(audit.verify_chain())
                raise UserError('rolled back on purpose')
        self.assertIsNone(audit.verify_chain())

    # -- the goods receiving page (client screen 9) --------------------------------------------------------------------------------
    def test_the_receiving_page_says_what_is_waiting_short_and_issued(self):
        picking = self.po.picking_ids
        self.assertEqual((picking.mos_receiving_status, picking.mos_item_count), ('waiting', 1))
        self.assertIn('40,000.0', picking.mos_ordered_totals)
        self.assertEqual(picking.mos_warehouse_id, picking.picking_type_id.warehouse_id)
        visit = self._visit_weighed()
        insp = self._inspect(visit)
        insp.with_user(self.manager).decide('accept_deduction', 2.0, 'Slight foreign matter')
        visit.with_user(self.guard).action_prepare_grn()
        self.assertEqual(picking.mos_receiving_status, 'short', 'the receipt is ready, with less accepted than ordered')
        self.assertEqual(picking.move_ids.mos_short_qty, 40000.0 - 34300.0)
        self.assertIn('34,300.0', picking.mos_accepted_totals)
        self.assertIn('5,700.0', picking.mos_short_totals)

    def test_the_person_who_generates_the_grn_is_the_receiving_officer(self):
        visit = self._visit_weighed()
        insp = self._inspect(visit)
        insp.with_user(self.manager).decide('accept_deduction', 2.0, 'Slight foreign matter')
        picking = visit.with_user(self.guard).action_prepare_grn()
        self.assertFalse(picking.user_id)
        picking.with_user(self.store).with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        self.assertEqual((picking.state, picking.user_id, picking.mos_receiving_status), ('done', self.store, 'done'))
