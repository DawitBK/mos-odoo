import base64
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import mute_logger
from psycopg2 import IntegrityError

PDF = base64.b64encode(b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n').decode()


@tagged('post_install', '-at_install')
class TestQuality(TransactionCase):
    """Library of methods and parameters, calculated lines, instruments, QC policy with waivers, CoA rule, lab queue, labels."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLQ2', 'warehouse_id': warehouse.id})
        cls.stock = warehouse.lot_stock_id

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('qguard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('qverifier2', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('qanalyst2', 'mos_lab.group_mos_lab_analyst')
        cls.manager = user('qmanager2', 'mos_lab.group_mos_lab_manager')
        cls.store = user('qstore', 'mos_inbound.group_mos_storekeeper')
        cls.buyer = user('qbuyer', 'purchase.group_purchase_user,mos_core.group_mos_user')
        kg = env.ref('uom.product_uom_kgm')
        cls.wheat = env['product.product'].create({'name': 'Quality wheat', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 20.0})
        cls.supplier = env['res.partner'].create({'name': 'Quality Supplier', 'supplier_rank': 1})
        cls.method = env['mos.lab.method'].create({'code': 'ICC-137', 'name': 'Moisture by oven', 'standard': 'ICC 110/1'})
        cls.moisture_param = env['mos.lab.parameter'].create({'name': 'Moisture', 'unit': '%', 'method_id': cls.method.id})
        cls.spec = env['mos.lab.spec'].create(
            {
                'product_id': cls.wheat.id,
                'line_ids': [
                    Command.create({'name': 'Moisture', 'unit': '%', 'min_value': 0, 'max_value': 13.5, 'parameter_id': cls.moisture_param.id, 'method_id': cls.method.id, 'code': 'moisture'}),
                    Command.create({'name': 'Wet gluten', 'unit': '%', 'min_value': 26, 'max_value': 40, 'code': 'wet_gluten'}),
                    Command.create({'name': 'Dry gluten', 'unit': '%', 'min_value': 8.0, 'max_value': 14.0, 'code': 'dry_gluten', 'formula': 'dry_gluten = wet_gluten * 0.33'}),
                ],
            }
        )
        cls.spec.action_approve()
        cls.l_moisture, cls.l_wet, cls.l_dry = cls.spec.line_ids

    # -- helpers -------------------------------------------------------------------------------------------------
    def _visit(self, plate, supplier=None):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': (supplier or self.supplier).id, 'order_line': [Command.create({'product_id': self.wheat.id, 'product_qty': 40000.0, 'price_unit': 20.0})]}
        )
        order.with_user(self.buyer).button_confirm()
        return self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': (supplier or self.supplier).id})

    def _weigh(self, visit, gross=50000.0, tare=15000.0):
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(gross)
        ticket.record_second(tare)
        ticket.with_user(self.verifier).verify_blind(gross, tare)

    def _inspect(self, visit, moisture=12.0, wet=30.0, decide=True, **extra):
        insp = self.env['mos.lab.inspection'].with_user(self.analyst).create({'visit_id': visit.id, 'product_id': self.wheat.id, **extra})
        insp.record_result(self.l_moisture, moisture)
        insp.record_result(self.l_wet, wet)
        insp.action_submit_results()
        if decide:
            insp.with_user(self.manager).decide('accept')
        return insp

    # -- library and calculated lines ------------------------------------------------------------------------------
    def test_library_fills_the_line_and_codes_are_checked(self):
        line = self.spec.line_ids[:1]
        self.assertEqual((line.parameter_id, line.method_id.standard), (self.moisture_param, 'ICC 110/1'))
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.env['mos.lab.method'].create({'code': 'ICC-137', 'name': 'Duplicate'})
        draft = self.env['mos.lab.spec'].create({'product_id': self.wheat.id, 'version': 9, 'line_ids': [Command.create({'name': 'A', 'min_value': 0, 'max_value': 1, 'code': 'a'})]})
        with self.assertRaises(ValidationError):
            draft.line_ids.code = 'bad code!'
        with self.assertRaises(ValidationError):
            draft.line_ids.formula = 'x = unknown_name * 2'

    def test_calculated_line_follows_the_measured_ones(self):
        visit = self._visit('Q-1')
        insp = self.env['mos.lab.inspection'].with_user(self.analyst).create({'visit_id': visit.id, 'product_id': self.wheat.id})
        insp.record_result(self.l_moisture, 12.0)
        self.assertEqual(insp.suggested_outcome, 'incomplete')
        insp.record_result(self.l_wet, 30.0)
        calc = insp.result_ids.filtered(lambda r: r.line_id == self.l_dry and r.valid)
        self.assertEqual(len(calc), 1)
        self.assertAlmostEqual(calc.value, 9.9)
        self.assertTrue(calc.calculated and calc.passed)
        self.assertEqual(insp.suggested_outcome, 'pass')
        insp.invalidate_recordset()
        insp.record_result(self.l_wet, 50.0)  # a correction: the dry gluten follows
        live = insp.result_ids.filtered(lambda r: r.line_id == self.l_dry and r.valid)
        self.assertEqual(len(live), 1)
        self.assertAlmostEqual(live.value, 16.5)
        self.assertFalse(live.passed)
        self.assertEqual(len(insp.result_ids.filtered(lambda r: r.line_id == self.l_dry)), 2, 'the earlier calculation stays on record, invalidated')

    # -- instruments -----------------------------------------------------------------------------------------------
    def test_results_need_an_instrument_that_is_in_calibration(self):
        meter = self.env['mos.lab.instrument'].create({'code': 'MM-1', 'name': 'Moisture meter', 'kind': 'moisture', 'interval_days': 90})
        self.assertEqual(meter.state, 'never')
        insp = self._inspect(self._visit('Q-2'), decide=False)
        with self.assertRaises(UserError):
            self.env['mos.lab.result'].create({'inspection_id': insp.id, 'line_id': self.l_moisture.id, 'value': 12.5, 'instrument_id': meter.id})
        with self.assertRaises(UserError):
            self.env['mos.lab.calibration'].with_user(self.analyst).create({'instrument_id': meter.id, 'result': 'pass'})  # only a lab manager
        self.env['mos.lab.calibration'].with_user(self.manager).create({'instrument_id': meter.id, 'result': 'pass', 'certificate_ref': 'CAL-2026-004', 'by': 'National metrology lab'})
        meter.invalidate_recordset()
        self.assertEqual(meter.state, 'ok')
        self.assertEqual(meter.next_due, fields.Date.today() + timedelta(days=90))
        result = self.env['mos.lab.result'].create({'inspection_id': insp.id, 'line_id': self.l_moisture.id, 'value': 12.5, 'instrument_id': meter.id})
        self.assertEqual(result.instrument_id, meter)
        self.env['mos.lab.calibration'].with_user(self.manager).create({'instrument_id': meter.id, 'result': 'fail', 'note': 'Drift of 0.6 %'})
        meter.invalidate_recordset()
        self.assertEqual(meter.state, 'out_of_service')
        with self.assertRaises(UserError):
            self.env['mos.lab.result'].create({'inspection_id': insp.id, 'line_id': self.l_moisture.id, 'value': 12.4, 'instrument_id': meter.id})

    def test_overdue_and_due_soon_states(self):
        meter = self.env['mos.lab.instrument'].create({'code': 'BAL-1', 'name': 'Balance', 'kind': 'balance', 'interval_days': 30})
        self.env['mos.lab.calibration'].with_user(self.manager).create({'instrument_id': meter.id, 'result': 'pass', 'date': fields.Date.today() - timedelta(days=40)})
        meter.invalidate_recordset()
        self.assertEqual(meter.state, 'overdue')
        self.env['mos.lab.calibration'].with_user(self.manager).create({'instrument_id': meter.id, 'result': 'pass', 'date': fields.Date.today() - timedelta(days=20)})
        meter.invalidate_recordset()
        self.assertEqual(meter.state, 'due_soon')
        with self.assertRaises(UserError):
            meter.calibration_ids[:1].write({'result': 'fail'})  # a calibration record is final

    # -- policy, waivers and skip-lot ------------------------------------------------------------------------------
    def test_a_product_that_needs_no_inspection_goes_to_receipt_without_one(self):
        self.env['mos.lab.policy'].create({'product_id': self.wheat.id, 'inspection_required': False})
        visit = self._visit('Q-3')
        self._weigh(visit)
        self.assertTrue(visit._mos_inspection_waived())
        self.assertIn('not inspected', visit.qc_waiver_note)
        picking = visit.with_user(self.guard).action_prepare_grn()
        self.assertEqual(picking.move_ids.quantity, 35000.0, 'the receipt follows the bridge weight')
        visit.with_user(self.store).action_confirm_grn()
        visit.action_clear_exit()
        visit.action_exit()
        self.assertEqual(visit.state, 'exited')

    def test_skip_lot_waves_proven_suppliers_through_one_delivery_in_n(self):
        self.env['mos.lab.policy'].create({'product_id': self.wheat.id, 'skip_lot': True, 'clean_streak': 2, 'skip_every': 3})
        first = self._visit('Q-4')
        self.assertFalse(first._mos_inspection_waived(), 'no history: inspected')
        self._inspect(first)
        second = self._visit('Q-5')
        self.assertFalse(second._mos_inspection_waived(), 'one clean inspection is not a streak')
        self._inspect(second)
        flags = []
        for n in range(6):
            visit = self._visit(f'Q-W{n}')
            waived = visit._mos_inspection_waived()
            flags.append(waived)
            if not waived:
                self._inspect(visit)
        self.assertEqual(flags, [True, True, False, True, True, False], 'two waved through, one inspected, over and over')
        again = self._visit('Q-6')
        again._mos_decide_waiver(self.wheat)
        self.assertEqual(again._mos_decide_waiver(self.wheat), again.qc_waived, 'decided once per visit')

    def test_a_failed_inspection_breaks_the_streak(self):
        self.env['mos.lab.policy'].create({'product_id': self.wheat.id, 'skip_lot': True, 'clean_streak': 2, 'skip_every': 3})
        for n, moisture in enumerate((12.0, 12.5)):
            self._inspect(self._visit(f'Q-B{n}'), moisture=moisture)
        bad = self._inspect(self._visit('Q-B9'), moisture=14.5, decide=False)
        bad.with_user(self.manager).decide('accept_deduction', 2.0, 'Concession')
        self.assertFalse(self._visit('Q-B10')._mos_inspection_waived(), 'a deduction in the recent history ends the skip')

    def test_policy_applies_by_category_and_needs_exactly_one_target(self):
        parent = self.env['product.category'].create({'name': 'Grain'})
        child = self.env['product.category'].create({'name': 'Wheat grain', 'parent_id': parent.id})
        self.wheat.categ_id = child
        self.env['mos.lab.policy'].create({'categ_id': parent.id, 'inspection_required': False})
        self.assertFalse(self.env['mos.lab.policy'].for_product(self.wheat).inspection_required, 'a policy on a parent category applies to the products below it')
        with self.assertRaises(ValidationError):
            self.env['mos.lab.policy'].create({'inspection_required': True})
        with self.assertRaises(ValidationError):
            self.env['mos.lab.policy'].create({'product_id': self.wheat.id, 'categ_id': parent.id})

    # -- supplier certificate ------------------------------------------------------------------------------------------
    def test_certificate_of_analysis_is_required_before_acceptance(self):
        self.env['mos.lab.policy'].create({'product_id': self.wheat.id, 'coa_required': True})
        visit = self._visit('Q-7')
        insp = self._inspect(visit, decide=False)
        with self.assertRaises(UserError):
            insp.with_user(self.manager).decide('accept')
        insp.with_user(self.manager).decide('reject', note='Out of spec anyway')  # rejecting never needs it
        visit2 = self._visit('Q-8')
        insp2 = self._inspect(visit2, decide=False)
        self.env['mos.attachment'].create({'name': 'coa-supplier.pdf', 'res_model': 'mos.gate.visit', 'res_id': visit2.id, 'file': PDF, 'kind': 'coa'})
        insp2.with_user(self.manager).decide('accept')
        self.assertEqual(insp2.state, 'decided')

    # -- queue and labels -----------------------------------------------------------------------------------------------
    def test_lab_queue_shows_waiting_work_and_out_of_spec(self):
        waiting = self._inspect(self._visit('Q-9'), decide=False)
        failing = self.env['mos.lab.inspection'].with_user(self.analyst).create({'visit_id': self._visit('Q-10').id, 'product_id': self.wheat.id, 'priority': 'urgent'})
        failing.record_result(self.l_moisture, 15.0)
        failing.record_result(self.l_wet, 30.0)
        self.env.flush_all()
        self.env.invalidate_all()
        rows = self.env['mos.lab.queue'].search([('inspection_id', 'in', (waiting | failing).ids)])
        by = {r.inspection_id: r for r in rows}
        self.assertEqual(by[waiting].waiting, 'decision')
        self.assertEqual(by[failing].waiting, 'results')
        self.assertTrue(by[failing].out_of_spec)
        self.assertEqual(by[failing].priority, 'urgent')
        waiting.with_user(self.manager).decide('accept')
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertNotIn(waiting, self.env['mos.lab.queue'].search([]).inspection_id)

    def test_sample_label_renders(self):
        insp = self._inspect(self._visit('Q-11'), decide=False)
        sample = insp.sample_ids
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_quality.report_sample_label', sample.ids)
        self.assertIn(sample.name.encode(), html)
        self.assertIn(b'barcode', html)
