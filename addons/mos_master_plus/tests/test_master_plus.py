from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestMasterPlus(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        cls.gm = new_test_user(cls.env, login='xconcgm', groups='mos_approvals.group_mos_general_manager')
        cls.gm.mos_site_ids = [Command.set(cls.site.ids)]

    # -- partners -----------------------------------------------------------------------------------------------------
    def test_a_tax_number_belongs_to_one_partner(self):
        partner = self.env['res.partner']
        first = partner.create({'name': 'Tax Owner One', 'vat': 'TIN 123 456'})
        with self.assertRaises(ValidationError):
            partner.create({'name': 'Tax Owner Two', 'vat': 'tin123456'})  # same number, spacing and case aside
        contact = partner.create({'name': 'Contact of one', 'parent_id': first.id, 'vat': 'TIN 123 456'})
        self.assertTrue(contact, 'a contact of the same business may carry it')
        partner.create({'name': 'No Tax Number Co'})
        partner.create({'name': 'Another Without'})  # empty numbers never clash

    def test_individual_supplier_needs_an_identifier_when_there_is_no_tin(self):
        partner = self.env['res.partner']
        with self.assertRaises(ValidationError):
            partner.create({'name': 'Farmer Without Papers', 'supplier_rank': 1, 'mos_individual': True})
        farmer = partner.create({'name': 'Farmer With Id', 'supplier_rank': 1, 'mos_individual': True, 'mos_national_id': 'ET-12345'})
        self.assertTrue(farmer)
        partner.create({'name': 'Trader With Tin', 'supplier_rank': 1, 'mos_individual': True, 'vat': 'TIN-TR-1'})
        partner.create({'name': 'Plain supplier', 'supplier_rank': 1})  # not flagged as an individual: unchanged behaviour

    def test_withholding_with_no_tin_follows_the_configured_rate(self):
        partner = self.env['res.partner']
        farmer = partner.create({'name': 'Farmer WHT', 'supplier_rank': 1, 'mos_individual': True, 'mos_national_id': 'ET-9'})
        with_tin = partner.create({'name': 'Trader WHT', 'supplier_rank': 1, 'mos_individual': True, 'vat': 'TIN-WHT-1'})
        self.assertEqual(farmer._mos_effective_wht_pct(), 2.0, 'no rate configured: the usual one')
        self.env['ir.config_parameter'].sudo().set_str('mos_master_plus.no_tin_wht_pct', '30')
        self.assertEqual(farmer._mos_effective_wht_pct(), 30.0)
        self.assertEqual(with_tin._mos_effective_wht_pct(), 2.0)
        self.assertEqual(self.s1._mos_effective_wht_pct(), 2.0, 'a company supplier is not touched')
        self.assertEqual(self.env['res.partner'].browse()._mos_effective_wht_pct(), 0.0)
        self.env['ir.config_parameter'].sudo().set_str('mos_master_plus.no_tin_wht_pct', 'abc')
        self.assertEqual(farmer._mos_effective_wht_pct(), 2.0, 'a bad value falls back')

    def test_a_name_like_an_existing_partner_is_flagged(self):
        partner = self.env['res.partner']
        partner.create({'name': 'Abebe Kebede Trading', 'supplier_rank': 1})
        typo = partner.create({'name': 'Abebe Kebede Tradng', 'supplier_rank': 1})
        self.assertIn('Abebe Kebede Trading', typo.mos_duplicate_warning)
        self.assertTrue(any('Looks like' in (m.body or '') for m in typo.message_ids))
        swapped = partner.create({'name': 'Kebede Abebe Trading', 'supplier_rank': 1})
        self.assertTrue(swapped.mos_duplicate_warning)
        different = partner.create({'name': 'Hawassa Flour Mills', 'supplier_rank': 1})
        self.assertFalse(different.mos_duplicate_warning)

    # -- item conversions -----------------------------------------------------------------------------------------------
    def test_a_used_conversion_never_changes(self):
        conv = self.env['mos.item.conversion']
        bag = conv.create({'product_id': self.wheat.id, 'unit_name': 'bag', 'factor': 50.0, 'effective_from': '2026-01-01'})
        self.assertEqual(conv.convert(self.wheat, 10, 'bag', date(2026, 6, 1)), 500.0)
        bag.factor = 49.0  # nobody used it yet: still editable
        bag.factor = 50.0
        self.assertEqual(conv.convert(self.wheat, 3, 'BAG', date(2026, 6, 1), use=True), 150.0, 'the unit name is not case sensitive')
        self.assertEqual(bag.use_count, 1)
        with self.assertRaises(UserError):
            bag.factor = 51.0
        with self.assertRaises(UserError):
            bag.unlink()
        newer = bag.action_new_version(48.0, date(2026, 7, 1))
        self.assertEqual(bag.effective_to, date(2026, 6, 30))
        self.assertEqual(conv.convert(self.wheat, 1, 'bag', date(2026, 6, 15)), 50.0, 'a document of June still reads 50')
        self.assertEqual(conv.convert(self.wheat, 1, 'bag', date(2026, 7, 15)), 48.0)
        self.assertEqual(conv.in_force(self.wheat, 'bag', date(2026, 8, 1)), newer)
        with self.assertRaises(UserError):
            conv.convert(self.wheat, 1, 'bale')
        with self.assertRaises(UserError):
            newer.action_new_version(47.0, date(2026, 6, 1))  # not after its own start
        with self.assertRaises(ValidationError):
            conv.create({'product_id': self.wheat.id, 'unit_name': 'sack', 'factor': 0})
        with self.assertRaises(ValidationError):
            conv.create({'product_id': self.wheat.id, 'unit_name': 'bag', 'factor': 51.0, 'effective_from': '2026-01-01'})  # same start

    # -- concessions ----------------------------------------------------------------------------------------------------
    def _failed_inspection(self, plate='MP-0001'):
        order = self._order()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': order.partner_id.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(50000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(50000.0, 15000.0)
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(self.line, 18.0)  # the specification allows 14
        insp.action_submit_results()
        self.assertEqual(insp.suggested_outcome, 'fail')
        return insp

    def _rule(self, doc_type='mos.concession', group='mos_approvals.group_mos_general_manager'):
        self.env['mos.approval.rule'].create({'doc_type': doc_type, 'amount_from': 0, 'group_id': self.env.ref(group).id})

    def test_without_the_setting_a_lab_manager_decides_alone(self):
        insp = self._failed_inspection()
        insp.with_user(self.labmgr).decide('accept', note='Customer agreed')
        self.assertEqual((insp.state, insp.decision), ('decided', 'accept'))
        self.assertFalse(self.env['mos.concession'].search([('inspection_id', '=', insp.id)]))

    def test_a_concession_waits_for_approval_and_the_analyst_never_gets_the_task(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_master_plus.concession_approval', 'True')
        self._rule()
        insp = self._failed_inspection('MP-0002')
        with self.assertRaises(UserError):
            insp.with_user(self.labmgr).decide('accept')  # no justification
        with self.assertRaises(UserError):
            insp.with_user(self.analyst).decide('accept', note='I tested it')  # not the analyst
        concession = insp.with_user(self.labmgr).decide('accept', note='Customer agreed to take it for feed')
        self.assertEqual((concession._name, concession.state), ('mos.concession', 'pending'))
        self.assertEqual(insp.state, 'tested', 'nothing is decided until the chain approves')
        step = concession.mos_approval_request_id.current_step_id
        self.assertNotIn(self.analyst, step.eligible_user_ids)
        self.assertNotIn(self.labmgr, step.eligible_user_ids)
        self.assertIn(self.gm, step.eligible_user_ids)
        with self.assertRaises(UserError):
            insp.with_user(self.labmgr).decide('accept', note='again')  # one request at a time
        step.with_user(self.gm).action_approve('Agreed')
        self.assertEqual((concession.state, insp.state, insp.decision), ('applied', 'decided', 'accept'))
        self.assertEqual(insp.decided_by_id, self.labmgr)

    def test_a_refused_concession_leaves_the_delivery_undecided(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_master_plus.concession_approval', 'True')
        self._rule()
        insp = self._failed_inspection('MP-0003')
        concession = insp.with_user(self.labmgr).decide('accept', note='Please')
        concession.mos_approval_request_id.current_step_id.with_user(self.gm).action_reject('Too wet')
        self.assertEqual((concession.state, insp.state), ('rejected', 'tested'))
        insp.with_user(self.labmgr).decide('reject', note='Wet wheat')
        self.assertEqual(insp.decision, 'reject')

    def test_a_concession_with_no_matching_rule_applies_at_once_and_a_rejection_is_never_held(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_master_plus.concession_approval', 'True')
        insp = self._failed_inspection('MP-0004')
        concession = insp.with_user(self.labmgr).decide('accept', note='No approver needed')
        self.assertEqual((concession.state, insp.state), ('applied', 'decided'))
        other = self._failed_inspection('MP-0005')
        other.with_user(self.labmgr).decide('reject')  # rejecting never needs a concession
        self.assertEqual(other.decision, 'reject')

    def test_severity_chooses_the_matrix_row(self):
        self.env['ir.config_parameter'].sudo().set_str('mos_master_plus.concession_approval', 'True')
        insp = self._failed_inspection('MP-0006')
        concession = self.env['mos.concession'].with_user(self.labmgr).create({'inspection_id': insp.id, 'decision': 'accept', 'justification': 'x'})
        self.assertEqual((concession.severity, concession._mos_doc_type()), ('minor', 'mos.concession'))
        concession.sudo().severity = 'major'
        self.assertEqual(concession._mos_doc_type(), 'mos.concession.major')

    # -- background reports ---------------------------------------------------------------------------------------------
    def test_a_big_report_is_queued_built_by_the_job_and_announced(self):
        order = self._order()
        self._receive(order, 'MP-0010')
        today = fields.Date.today()
        job = self.env['mos.report.job'].with_user(self.requester).create({'report': 'stock_movements', 'date_from': today - timedelta(days=365), 'date_to': today})
        self.assertEqual(job.state, 'queued')
        self.assertTrue(job.name.startswith('RPT-'))
        self.assertEqual(self.env['mos.report.job']._cron_process(), 1)
        self.assertEqual(job.state, 'done')
        raw = job.file
        text = (raw.content if hasattr(raw, 'content') else raw).decode()
        self.assertTrue(text.startswith('Date,Code,Item,Reference,Kind'))
        self.assertGreaterEqual(job.rows, 1)
        self.assertEqual(len(text.strip().splitlines()), job.rows + 1)
        told = self.env['mail.message'].search([('partner_ids', 'in', self.requester.partner_id.ids), ('body', 'ilike', 'rows')])
        self.assertTrue(told, 'the requester was told')
        valuation = self.env['mos.report.job'].create({'report': 'receipt_valuation', 'date_from': today - timedelta(days=30), 'date_to': today})
        valuation.action_run()
        self.assertEqual(valuation.state, 'done')
        audit = self.env['mos.report.job'].create({'report': 'audit_trail', 'date_from': today, 'date_to': today})
        audit.action_run()
        self.assertGreaterEqual(audit.rows, 1)
        with self.assertRaises(UserError):
            job.action_run()  # already built
        with self.assertRaises(UserError):
            self.env['mos.report.job'].create({'report': 'audit_trail', 'date_from': today, 'date_to': today - timedelta(days=1)})

    def test_a_failed_report_is_recorded_and_does_not_stop_the_queue(self):
        today = fields.Date.today()
        bad = self.env['mos.report.job'].create({'report': 'audit_trail', 'date_from': today, 'date_to': today})
        good = self.env['mos.report.job'].create({'report': 'receipt_valuation', 'date_from': today, 'date_to': today})
        original = type(bad)._data

        def flaky(job):
            if job.id == bad.id:
                raise RuntimeError('boom')
            return original(job)

        with patch.object(type(bad), '_data', flaky):
            self.env['mos.report.job']._cron_process()
        self.assertEqual((bad.state, good.state), ('failed', 'done'))
        self.assertIn('boom', bad.error)
        self.assertTrue(self.env['mail.message'].search([('partner_ids', 'in', bad.requested_by_id.partner_id.ids), ('body', 'ilike', 'boom')]), 'the requester was told why')
