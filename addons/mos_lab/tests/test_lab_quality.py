from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestLabQuality(TransactionCase):
    """Rule-based deductions, score and grade, sampling plans, retention samples, certificates, report."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLQ', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('qguard', 'mos_gate.group_mos_gate_guard')
        cls.analyst = user('qanalyst', 'mos_lab.group_mos_lab_analyst')
        cls.manager = user('qmanager', 'mos_lab.group_mos_lab_manager')
        cls.verifier = user('qverifier', 'mos_gate.group_mos_gate_verifier')
        cls.product = env['product.product'].create(
            {'name': 'Bread wheat', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id}
        )
        cls.spec = env['mos.lab.spec'].create(
            {
                'product_id': cls.product.id,
                'line_ids': [
                    Command.create({'name': 'Moisture %', 'min_value': 0, 'max_value': 13.5, 'weight': 2.0}),
                    Command.create({'name': 'Foreign matter %', 'min_value': 0, 'max_value': 2.0, 'weight': 1.0}),
                ],
                'grade_ids': [Command.create({'name': 'Grade A', 'min_score': 100}), Command.create({'name': 'Grade B', 'min_score': 60}), Command.create({'name': 'Feed', 'min_score': 0})],
            }
        )
        cls.moisture, cls.foreign = cls.spec.line_ids
        cls.env['mos.lab.deduction.rule'].create({'line_id': cls.moisture.id, 'from_value': 13.6, 'to_value': 15.0, 'weight_deduction_pct': 1.5, 'price_adjust_pct': -2.0})
        cls.env['mos.lab.deduction.rule'].create({'line_id': cls.moisture.id, 'from_value': 15.01, 'to_value': 30.0, 'weight_deduction_pct': 4.0, 'price_adjust_pct': -6.0})
        cls.spec.action_approve()

    def _visit(self, plate='LQ-1', purpose='outbound_do', weight=30000.0):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purpose': purpose})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        if purpose == 'outbound_do':
            ticket.record_first(10000.0)
            ticket.record_second(10000.0 + weight)
            ticket.with_user(self.verifier).verify_blind(10000.0, 10000.0 + weight)
        return visit

    def _inspection(self, moisture, foreign, visit=None, **extra):
        visit = visit or self._visit()
        insp = self.env['mos.lab.inspection'].with_user(self.analyst).create({'visit_id': visit.id, 'product_id': self.product.id, **extra})
        insp.record_result(self.moisture, moisture)
        insp.record_result(self.foreign, foreign)
        return insp


    # -- the QC summary on the inspection screen --------------------------------------------------------
    def test_summary_counts_and_result_pills(self):
        insp = self._inspection(14.2, 1.0)
        self.assertEqual((insp.params_total, insp.params_passed, insp.params_failed), (2, 1, 1))
        outcomes = {r.line_id.name: r.outcome for r in insp.result_ids}
        self.assertEqual(outcomes, {'Moisture %': 'fail', 'Foreign matter %': 'pass'})
        self.assertEqual(insp.result_ids.filtered(lambda r: r.line_id == self.moisture).range_text, '<= 13.5')
        wrong = insp.result_ids.filtered(lambda r: r.line_id == self.moisture)
        wrong.action_invalidate('typed wrong')
        self.assertEqual(wrong.outcome, 'invalid')
        self.assertEqual(insp.params_failed, 0, 'an invalidated result no longer counts')

    # -- rules, score, grade ----------------------------------------------------------------------------
    def test_rule_based_deduction_score_and_grade(self):
        insp = self._inspection(14.2, 1.0)
        self.assertEqual(insp.auto_deduction_pct, 1.5)
        self.assertEqual(insp.auto_price_adjust_pct, -2.0)
        self.assertEqual(insp.score, 33.3, 'only foreign matter (weight 1 of 3) is inside its limits')
        self.assertEqual(insp.grade_id.name, 'Feed')
        clean = self._inspection(12.0, 1.0, visit=self._visit('LQ-2'))
        self.assertEqual((clean.score, clean.grade_id.name, clean.auto_deduction_pct), (100.0, 'Grade A', 0.0))

    def test_decision_applies_the_rule_deduction(self):
        insp = self._inspection(14.2, 1.0)
        insp.action_submit_results()
        net = insp.net_weight
        insp.with_user(self.manager).decide('accept', note='Within concession')
        self.assertEqual(insp.decision, 'accept_deduction', 'a rule deduction turns a plain accept into an accept with deduction')
        self.assertEqual(insp.deduction_pct, 1.5)
        self.assertEqual(insp.price_adjust_pct, -2.0)
        self.assertEqual(insp.payable_qty, round(net * (1 - 0.015), 1))

    def test_going_below_the_rule_needs_a_manager_and_a_note(self):
        insp = self._inspection(16.0, 1.0)
        insp.action_submit_results()
        self.assertEqual(insp.auto_deduction_pct, 4.0)
        with self.assertRaises(UserError):
            insp.with_user(self.manager).decide('accept_deduction', 1.0)  # no note
        insp.with_user(self.manager).decide('accept_deduction', 1.0, 'Long-standing supplier')
        self.assertEqual(insp.deduction_pct, 1.0)

    def test_rule_bands_validate_and_freeze_with_the_specification(self):
        draft = self.env['mos.lab.spec'].create({'product_id': self.product.id, 'version': 9, 'line_ids': [Command.create({'name': 'X', 'min_value': 0, 'max_value': 5})]})
        line = draft.line_ids
        with self.assertRaises(ValidationError):
            self.env['mos.lab.deduction.rule'].create({'line_id': line.id, 'from_value': 5, 'to_value': 1})
        self.env['mos.lab.deduction.rule'].create({'line_id': line.id, 'from_value': 6, 'to_value': 8, 'weight_deduction_pct': 1})
        with self.assertRaises(ValidationError):
            self.env['mos.lab.deduction.rule'].create({'line_id': line.id, 'from_value': 7, 'to_value': 9, 'weight_deduction_pct': 1})
        with self.assertRaises(UserError):
            self.env['mos.lab.deduction.rule'].create({'line_id': self.moisture.id, 'from_value': 40, 'to_value': 50})  # approved spec
        with self.assertRaises(UserError):
            self.moisture.rule_ids[:1].write({'weight_deduction_pct': 9})

    def test_new_version_carries_rules_and_grades(self):
        new = self.spec.action_new_version()
        self.assertEqual(len(new.grade_ids), 3)
        self.assertEqual(len(new.line_ids.rule_ids), 2)

    # -- sampling plan and retention samples -----------------------------------------------------------
    def test_sampling_plan_requires_samples(self):
        plan = self.env['mos.lab.sampling.plan'].create(
            {'product_id': self.product.id, 'line_ids': [Command.create({'from_kg': 0, 'samples': 2}), Command.create({'from_kg': 20000, 'samples': 4})]}
        )
        self.assertEqual(self.env['mos.lab.sampling.plan'].samples_required(self.product, 5000), 2)
        insp = self._inspection(12.0, 1.0)
        self.assertEqual(insp.samples_required, 4, 'a 30 t consignment falls in the 20 t band')
        with self.assertRaises(UserError):
            insp.action_submit_results()
        insp.samples_taken = 4
        insp.action_submit_results()
        self.assertEqual(insp.state, 'tested')
        self.assertTrue(plan)

    def test_retention_sample_is_created_and_guarded(self):
        insp = self._inspection(12.0, 1.0)
        insp.action_submit_results()
        sample = insp.sample_ids
        self.assertEqual(len(sample), 1)
        self.assertRegex(sample.name, r'^RS-\d{4}-\d{5}$')
        self.assertEqual(sample.retain_until, fields.Date.today() + timedelta(days=90))
        self.assertFalse(sample.due)
        with self.assertRaises(UserError):
            sample.with_user(self.analyst).action_discard()  # not due, no reason
        with self.assertRaises(UserError):
            sample.with_user(self.analyst).action_discard('Spilled')  # not a manager
        sample.with_user(self.manager).action_discard('Spilled')
        self.assertEqual(sample.state, 'discarded')
        old = self.env['mos.lab.sample'].create({'inspection_id': insp.id, 'stored_on': fields.Date.today() - timedelta(days=120)})
        self.assertTrue(old.due)
        self.assertIn(old, self.env['mos.lab.sample'].search([('due', '=', True)]))
        self.assertNotIn(old, self.env['mos.lab.sample'].search([('due', '=', False)]))
        old.with_user(self.analyst).action_discard()
        self.assertEqual(old.state, 'discarded')

    # -- results from the UI path -----------------------------------------------------------------------
    def test_results_validate_on_create_and_reopen(self):
        insp = self._inspection(12.0, 1.0)
        insp.action_submit_results()
        self.assertEqual(insp.state, 'tested')
        other = self.env['mos.lab.spec'].create({'product_id': self.product.id, 'version': 7, 'line_ids': [Command.create({'name': 'Other', 'min_value': 0, 'max_value': 1})]})
        with self.assertRaises(UserError):
            self.env['mos.lab.result'].with_user(self.analyst).create({'inspection_id': insp.id, 'line_id': other.line_ids.id, 'value': 1})
        self.env['mos.lab.result'].with_user(self.analyst).create({'inspection_id': insp.id, 'line_id': self.moisture.id, 'value': 12.5})
        self.assertEqual(insp.state, 'open', 'a new result reopens a submitted inspection')

    # -- certificate of analysis ------------------------------------------------------------------------
    def test_coa_gate_on_outbound_exit(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_lab.require_coa', 'True')
        visit = self._visit('CO-1')
        problems = visit._exit_blockers()
        self.assertTrue(any('certificate of analysis' in p for p in problems))
        insp = self._inspection(12.0, 1.0, visit=visit)
        insp.action_submit_results()
        insp.with_user(self.manager).decide('accept')
        coa = self.env['mos.lab.coa'].with_user(self.manager).issue(insp, visit)
        self.assertRegex(coa.name, r'^COA-\d{4}-\d{5}$')
        self.assertIn('Moisture %: 12', coa.results_text)
        self.assertEqual(coa.grade_id.name, 'Grade A')
        self.assertFalse(any('certificate of analysis' in p for p in visit._exit_blockers()))
        with self.assertRaises(UserError):
            coa.write({'score': 1})  # issued certificates are immutable
        with self.assertRaises(UserError):
            coa.action_revoke('')
        coa.action_revoke('Wrong lot')
        self.assertEqual(coa.state, 'revoked')
        self.assertTrue(any('certificate of analysis' in p for p in visit._exit_blockers()))

    def test_coa_needs_an_accepted_decision(self):
        visit = self._visit('CO-2')
        insp = self._inspection(16.0, 5.0, visit=visit)
        insp.action_submit_results()
        with self.assertRaises(UserError):
            self.env['mos.lab.coa'].issue(insp, visit)  # not decided
        insp.with_user(self.manager).decide('reject', note='Failed')
        with self.assertRaises(UserError):
            self.env['mos.lab.coa'].issue(insp, visit)

    # -- report ------------------------------------------------------------------------------------------
    def test_lab_report(self):
        insp = self._inspection(14.2, 1.0)
        insp.action_submit_results()
        insp.with_user(self.manager).decide('accept', note='ok')
        self.env.flush_all()
        row = self.env['mos.lab.report'].search([('inspection_id', '=', insp.id)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.net_weight, 30000.0)
        self.assertEqual(row.deducted_kg, round(30000.0 - insp.payable_qty, 1))
        self.assertEqual(row.rejected_kg, 0.0)
