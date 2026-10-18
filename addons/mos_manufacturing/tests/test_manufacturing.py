from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestManufacturing(TransactionCase):
    """Batches and tracing, the hold on finished flour, packing, fortification, downtime, cost and the planner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.warehouse = warehouse
        cls.stock = warehouse.lot_stock_id
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLM', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('mguard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('mverifier', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('manalyst', 'mos_lab.group_mos_lab_analyst')
        cls.labmgr = user('mlabmgr', 'mos_lab.group_mos_lab_manager')
        cls.store = user('mstore', 'mos_inbound.group_mos_storekeeper')
        cls.sup = user('msup', 'mos_manufacturing.group_mos_production_supervisor')
        cls.buyer = user('mbuyer', 'purchase.group_purchase_user,mos_core.group_mos_user')
        kg = env.ref('uom.product_uom_kgm')
        unit = env.ref('uom.product_uom_unit')

        def product(name, uom=kg, cost=10.0, **extra):
            return env['product.product'].create({'name': name, 'type': 'consu', 'is_storable': True, 'uom_id': uom.id, 'standard_price': cost, **extra})

        cls.wheat = product('Mill wheat', cost=20.0)
        cls.flour = product('Bread flour', cost=0.0)
        cls.bran = product('Bran', cost=0.0, mos_cost_factor=0.2)
        cls.bag = product('Flour 50 kg bag', uom=unit, mos_bag_kg=50.0)
        cls.premix = product('Premix')
        cls.silo = env['mos.silo'].create({'name': 'Silo 1', 'code': 'S1', 'site_id': cls.site.id, 'capacity_kg': 200000, 'product_id': cls.wheat.id})
        cls.supplier = env['res.partner'].create({'name': 'Batch Supplier', 'supplier_rank': 1})
        wheat_spec = env['mos.lab.spec'].create({'product_id': cls.wheat.id, 'line_ids': [Command.create({'name': 'Moisture %', 'min_value': 0, 'max_value': 13.5})]})
        wheat_spec.action_approve()
        cls.wheat_line = wheat_spec.line_ids
        flour_spec = env['mos.lab.spec'].create({'product_id': cls.flour.id, 'line_ids': [Command.create({'name': 'Ash %', 'min_value': 0.4, 'max_value': 0.8})]})
        flour_spec.action_approve()
        cls.flour_line = flour_spec.line_ids
        cls.centre = env['mos.work.centre'].create({'name': 'Mill line 1', 'code': 'ML1', 'site_id': cls.site.id, 'kind': 'milling'})

    # -- helpers -------------------------------------------------------------------------------------------------
    def _set_stock(self, product, location, qty):
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id, 'location_id': location.id, 'inventory_quantity': qty})
        quant.action_apply_inventory()

    def _qty(self, location, product):
        return sum(self.env['stock.quant'].search([('product_id', '=', product.id), ('location_id', '=', location.id)]).mapped('quantity'))

    def _receive_into_silo(self, qty=40000.0, plate='M-1'):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.wheat.id, 'product_qty': qty + 5000, 'price_unit': 20.0})]}
        )
        order.with_user(self.buyer).button_confirm()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': self.supplier.id, 'silo_id': self.silo.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(qty + 15000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(qty + 15000.0, 15000.0)
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(self.wheat_line, 12.0)
        insp.action_submit_results()
        insp.with_user(self.labmgr).decide('accept')
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        return visit

    def _run(self, wheat=20000.0, flour=15000.0, bran=4000.0, **extra):
        return self.env['mos.milling.run'].with_user(self.sup).create(
            {
                'site_id': self.site.id,
                'run_date': fields.Date.today(),
                'work_centre_id': self.centre.id,
                'input_ids': [Command.create({'silo_id': self.silo.id, 'product_id': self.wheat.id, 'qty_kg': wheat})],
                'output_ids': [
                    Command.create({'product_id': self.flour.id, 'kind': 'flour', 'qty_kg': flour}),
                    Command.create({'product_id': self.bran.id, 'kind': 'bran', 'qty_kg': bran}),
                ],
                **extra,
            }
        )

    def _release(self, batch, ash=0.6):
        insp = self.env['mos.batch.inspection'].with_user(self.analyst).create({'batch_id': batch.id})
        insp.record_result(self.flour_line, ash)
        insp.action_submit()
        insp.with_user(self.labmgr).decide('release')
        return insp

    # -- batches and tracing -------------------------------------------------------------------------------------
    def test_milling_makes_batches_and_traces_back_to_the_delivery(self):
        visit = self._receive_into_silo()
        wheat_batch = visit.env['mos.batch'].search([('visit_id', '=', visit.id)])
        self.assertEqual((wheat_batch.kind, wheat_batch.qty, wheat_batch.supplier_id), ('wheat', 40000.0, self.supplier))
        run = self._run()
        run.with_user(self.sup).action_complete()
        self.assertEqual(run.state, 'done')
        self.assertEqual(wheat_batch.remaining_qty, 20000.0)
        flour_batch = run.batch_ids.filtered(lambda b: b.kind == 'flour')
        self.assertEqual((flour_batch.qty, flour_batch.state), (15000.0, 'released'))
        back = [(level, batch.id, qty) for level, batch, qty in flour_batch.trace_back()]
        self.assertEqual([b[1] for b in back], [wheat_batch.id])
        self.assertAlmostEqual(back[0][2], 20000.0 * 15000.0 / 19000.0, places=1)
        forward = wheat_batch.trace_forward()
        self.assertEqual({b.id for _l, b, _q, _n in forward}, set(run.batch_ids.ids))
        trace = self.env['mos.trace'].create({'batch_id': flour_batch.id})
        trace.action_build()
        self.assertTrue(any('from Batch Supplier' in (line.note or '') for line in trace.line_ids))

    def test_wheat_the_system_never_saw_arrive_becomes_an_opening_batch(self):
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run(wheat=10000.0, flour=7500.0, bran=2000.0)
        run.action_complete()
        opening = self.env['mos.batch'].search([('kind', '=', 'wheat'), ('silo_id', '=', self.silo.id), ('visit_id', '=', False)])
        self.assertEqual(opening.qty, 10000.0)
        self.assertIn('Opening stock', opening.message_ids[:1].body or opening.message_ids.mapped('body')[0])

    def test_material_cost_is_settled_over_the_outputs(self):
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run(wheat=20000.0, flour=15000.0, bran=4000.0)
        run.action_complete()
        self.assertEqual(run.input_value, 400000.0)
        weights = 15000.0 * 1.0 + 4000.0 * 0.2
        self.assertAlmostEqual(run.unit_cost_flour, 400000.0 / weights, places=3)
        flour_line = run.output_ids.filtered(lambda o: o.kind == 'flour')
        bran_line = run.output_ids.filtered(lambda o: o.kind == 'bran')
        self.assertAlmostEqual(flour_line.cost_value + bran_line.cost_value, 400000.0, delta=0.05)
        self.assertLess(bran_line.unit_cost, flour_line.unit_cost)

    # -- the hold on finished flour ------------------------------------------------------------------------------
    def test_flour_is_held_until_quality_releases_it(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_manufacturing.hold_flour', 'True')
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run()
        run.action_complete()
        batch = run.batch_ids.filtered(lambda b: b.kind == 'flour')
        quarantine = self.env['mos.stock.status.change'].status_location(self.site, 'quarantine')
        self.assertEqual(batch.state, 'hold')
        self.assertEqual(self._qty(quarantine, self.flour), 15000.0)
        self.assertEqual(self._qty(self.stock, self.flour), 0.0)
        insp = self.env['mos.batch.inspection'].with_user(self.analyst).create({'batch_id': batch.id})
        insp.record_result(self.flour_line, 0.6)
        insp.action_submit()
        with self.assertRaises(UserError):
            insp.with_user(self.analyst).decide('release')  # not a lab manager
        manager_tested = self.env['mos.batch.inspection'].with_user(self.labmgr).create({'batch_id': batch.id})
        manager_tested.record_result(self.flour_line, 0.6)
        manager_tested.action_submit()
        with self.assertRaises(UserError):
            manager_tested.with_user(self.labmgr).decide('release')  # the person who tested cannot decide
        insp.with_user(self.labmgr).decide('release')
        self.assertEqual(batch.state, 'released')
        self.assertEqual(self._qty(self.stock, self.flour), 15000.0)
        self.assertEqual(self._qty(quarantine, self.flour), 0.0)

    def test_rejected_flour_is_blocked_and_a_failed_release_needs_a_reason(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_manufacturing.hold_flour', 'True')
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run()
        run.action_complete()
        batch = run.batch_ids.filtered(lambda b: b.kind == 'flour')
        insp = self.env['mos.batch.inspection'].with_user(self.analyst).create({'batch_id': batch.id})
        insp.record_result(self.flour_line, 1.1)  # ash above the limit
        insp.action_submit()
        self.assertEqual(insp.suggested, 'fail')
        with self.assertRaises(UserError):
            insp.with_user(self.labmgr).decide('release')  # a failed batch needs a justification
        insp.with_user(self.labmgr).decide('reject', 'Ash too high')
        self.assertEqual(batch.state, 'rejected')
        blocked = self.env['mos.stock.status.change'].status_location(self.site, 'blocked')
        self.assertEqual(self._qty(blocked, self.flour), 15000.0)

    def test_wheat_has_no_finished_goods_inspection(self):
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run()
        run.action_complete()
        wheat_batch = self.env['mos.batch'].search([('kind', '=', 'wheat'), ('silo_id', '=', self.silo.id)], limit=1)
        with self.assertRaises(UserError):
            self.env['mos.batch.inspection'].create({'batch_id': wheat_batch.id})

    # -- packing -------------------------------------------------------------------------------------------------
    def test_packing_turns_released_flour_into_bag_batches(self):
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run()
        run.action_complete()
        batch = run.batch_ids.filtered(lambda b: b.kind == 'flour')
        pack = self.env['mos.pack.run'].with_user(self.sup).create(
            {'site_id': self.site.id, 'source_batch_id': batch.id, 'spillage_kg': 20.0, 'line_ids': [Command.create({'product_id': self.bag.id, 'bags': 100})]}
        )
        self.assertEqual((pack.packed_kg, pack.used_kg), (5000.0, 5020.0))
        self.assertAlmostEqual(pack.yield_pct, 5000.0 / 5020.0 * 100.0, places=2)
        pack.with_user(self.sup).action_complete()
        self.assertEqual(pack.state, 'done')
        self.assertEqual(self._qty(self.stock, self.bag), 100.0)
        self.assertEqual(self._qty(self.stock, self.flour), 15000.0 - 5020.0)
        self.assertEqual(batch.remaining_qty, 15000.0 - 5020.0)
        bags = pack.line_ids.batch_id
        self.assertEqual((bags.kind, bags.qty, bags.pack_run_id), ('packed', 100.0, pack))
        self.assertEqual(bags.trace_back()[0][1], batch)
        with self.assertRaises(UserError):
            pack.line_ids.write({'bags': 5})  # a completed run is final

    def test_packing_refuses_held_flour_and_too_much(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_manufacturing.hold_flour', 'True')
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        run = self._run()
        run.action_complete()
        batch = run.batch_ids.filtered(lambda b: b.kind == 'flour')
        held = self.env['mos.pack.run'].create({'site_id': self.site.id, 'source_batch_id': batch.id, 'line_ids': [Command.create({'product_id': self.bag.id, 'bags': 10})]})
        with self.assertRaises(UserError):
            held.action_complete()  # quality control has not released the batch
        self._release(batch)
        big = self.env['mos.pack.run'].create({'site_id': self.site.id, 'source_batch_id': batch.id, 'line_ids': [Command.create({'product_id': self.bag.id, 'bags': 400})]})
        with self.assertRaises(UserError):
            big.action_complete()  # 20 t from a 15 t batch
        with self.assertRaises(ValidationError):
            self.env['mos.pack.run.line'].create({'run_id': big.id, 'product_id': self.bag.id, 'bags': 0})

    # -- fortification -------------------------------------------------------------------------------------------
    def test_fortification_dose_must_match_the_target(self):
        self.flour.product_tmpl_id.write({'mos_premix_id': self.premix.id, 'mos_premix_g_per_t': 100.0})
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        self._set_stock(self.premix, self.stock, 5.0)
        run = self._run()  # 15 t of flour needs 1.5 kg
        with self.assertRaises(UserError):
            run.action_complete()  # no dosing recorded
        dose = self.env['mos.dosing.line'].create({'run_id': run.id, 'premix_id': self.premix.id, 'actual_kg': 1.0})
        self.assertAlmostEqual(dose.target_kg, 1.5)
        self.assertAlmostEqual(dose.variance_pct, -33.3, places=1)
        with self.assertRaises(UserError):
            run.action_complete()  # one third short
        dose.actual_kg = 1.45
        run.action_complete()
        self.assertAlmostEqual(self._qty(self.stock, self.premix), 5.0 - 1.45, places=3)
        with self.assertRaises(UserError):
            dose.write({'actual_kg': 9})

    def test_fortified_flour_needs_a_premix_product(self):
        self.flour.product_tmpl_id.write({'mos_premix_id': False, 'mos_premix_g_per_t': 100.0})
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        with self.assertRaises(UserError):
            self._run().action_complete()

    # -- work centres, downtime, shift logs ------------------------------------------------------------------------
    def test_downtime_cannot_overlap_and_shift_logs_are_one_per_shift(self):
        reason = self.env['mos.reason'].create({'domain': 'downtime', 'code': 'JAM', 'name': 'Roller jam'})
        start = fields.Datetime.now()
        self.env['mos.downtime'].with_user(self.sup).create({'work_centre_id': self.centre.id, 'start': start, 'end': start + timedelta(hours=1), 'reason_id': reason.id})
        with self.assertRaises(ValidationError):
            self.env['mos.downtime'].create({'work_centre_id': self.centre.id, 'start': start + timedelta(minutes=30), 'end': start + timedelta(hours=2), 'reason_id': reason.id})
        other_line = self.env['mos.work.centre'].create({'name': 'Mill line 2', 'code': 'ML2', 'site_id': self.site.id})
        open_stop = self.env['mos.downtime'].create({'work_centre_id': other_line.id, 'start': start - timedelta(hours=3), 'reason_id': reason.id})
        open_stop.action_end_now()
        self.assertTrue(open_stop.end)
        self.env['mos.shift.log'].with_user(self.sup).create({'work_centre_id': self.centre.id, 'notes': 'Normal shift', 'shift': 'day'})
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['mos.shift.log'].create({'work_centre_id': self.centre.id, 'notes': 'Second log', 'shift': 'day'})

    # -- delivery allocation and the reports ----------------------------------------------------------------------
    def test_a_delivery_is_tied_to_the_oldest_released_batches(self):
        self._set_stock(self.wheat, self.silo.location_id, 60000.0)
        first = self._run(wheat=20000.0, flour=15000.0, bran=4000.0)
        first.action_complete()
        second = self._run(wheat=10000.0, flour=7500.0, bran=2000.0)
        second.action_complete()
        customer = self.env['res.partner'].create({'name': 'Bakery'})
        order = self.env['sale.order'].create({'partner_id': customer.id, 'order_line': [Command.create({'product_id': self.flour.id, 'product_uom_qty': 20000.0, 'price_unit': 30.0})]})
        delivery = self.env['mos.delivery.order'].create({'sale_id': order.id, 'site_id': self.site.id})
        delivery._mos_allocate_batches(self.flour, 20000.0)
        rows = self.env['mos.batch.delivery'].search([('delivery_id', '=', delivery.id)], order='id')
        self.assertEqual(rows.mapped('qty'), [15000.0, 5000.0])
        self.assertEqual(rows[0].batch_id, first.batch_ids.filtered(lambda b: b.kind == 'flour'))
        self.assertEqual(rows.batch_id[1].remaining_qty, 2500.0)
        more = self.env['mos.batch.delivery'].search_count([('delivery_id', '=', delivery.id), ('untraced', '=', True)])
        self.assertEqual(more, 0)
        delivery._mos_allocate_batches(self.flour, 5000.0)
        untraced = self.env['mos.batch.delivery'].search([('delivery_id', '=', delivery.id), ('untraced', '=', True)])
        self.assertEqual(untraced.qty, 2500.0, 'what no batch can cover is recorded as untraced')
        forward = first.batch_ids.filtered(lambda b: b.kind == 'flour').trace_forward()
        self.assertTrue(any('delivered to Bakery' in note for _l, _b, _q, note in forward))

    def test_production_and_planner_reports(self):
        self._set_stock(self.wheat, self.silo.location_id, 30000.0)
        self._set_stock(self.flour, self.stock, 3000.0)
        run = self._run()
        run.action_complete()
        customer = self.env['res.partner'].create({'name': 'Planner customer'})
        order = self.env['sale.order'].create({'partner_id': customer.id, 'order_line': [Command.create({'product_id': self.flour.id, 'product_uom_qty': 25000.0, 'price_unit': 30.0})]})
        order.sudo().write({'state': 'sale'})  # confirmation has its own approval rules; the report only needs a confirmed order
        self.env.flush_all()
        self.env.invalidate_all()
        row = self.env['mos.production.report'].search([('id', '=', run.id)])
        self.assertEqual((row.wheat_kg, row.flour_kg), (20000.0, 15000.0))
        planner = self.env['mos.planner.report'].search([('product_id', '=', self.flour.id)])
        self.assertEqual(planner.open_demand, 25000.0)
        self.assertEqual(planner.available, 18000.0)
        self.assertEqual(planner.shortfall, 7000.0)

    def test_work_centre_codes_are_unique_per_site(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env['mos.work.centre'].create({'name': 'Duplicate', 'code': 'ML1', 'site_id': self.site.id})
