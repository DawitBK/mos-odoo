from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestSequencePolicy(TransactionCase):
    """FR-PLT-040: a pattern with a fiscal-year token, and a restart policy of never, each fiscal year, each calendar year or each month."""

    def _seq(self, reset, prefix='T-%(fy)s-%(range_year)s%(range_month)s-', **extra):
        return self.env['ir.sequence'].create({'name': f'Test {reset}', 'code': f'test.seq.{reset}', 'prefix': prefix, 'padding': 3, 'implementation': 'no_gap', 'mos_reset': reset, **extra})

    def _next(self, seq, day):
        return seq.next_by_id(sequence_date=day)

    def test_never_counts_on_for_ever(self):
        seq = self._seq('never', prefix='NV-')
        self.assertFalse(seq.use_date_range)
        self.assertEqual([self._next(seq, '2026-01-05'), self._next(seq, '2027-06-05')], ['NV-001', 'NV-002'])

    def test_calendar_restarts_each_year(self):
        seq = self._seq('calendar', prefix='CY-%(range_year)s-')
        self.assertEqual([self._next(seq, '2026-12-30'), self._next(seq, '2026-12-31'), self._next(seq, '2027-01-02')], ['CY-2026-001', 'CY-2026-002', 'CY-2027-001'])

    def test_month_restarts_each_month(self):
        seq = self._seq('month', prefix='M-%(range_year)s%(range_month)s-')
        self.assertEqual([self._next(seq, '2026-03-30'), self._next(seq, '2026-03-31'), self._next(seq, '2026-04-01')], ['M-202603-001', 'M-202603-002', 'M-202604-001'])

    def test_fiscal_year_restarts_when_the_company_year_ends_and_the_token_names_it(self):
        company = self.env.company
        company.write({'fiscalyear_last_month': '6', 'fiscalyear_last_day': 30})
        seq = self._seq('fiscal', prefix='FY%(fy)s-')
        self.assertEqual(
            [self._next(seq, '2026-06-30'), self._next(seq, '2026-07-01'), self._next(seq, '2027-06-30'), self._next(seq, '2027-07-01')],
            ['FY2025-26-001', 'FY2026-27-001', 'FY2026-27-002', 'FY2027-28-001'],
        )

    def test_the_fiscal_year_label_is_the_plain_year_when_it_is_the_calendar_year(self):
        self.env.company.write({'fiscalyear_last_month': '12', 'fiscalyear_last_day': 31})
        seq = self._seq('fiscal', prefix='FY%(fy)s-')
        self.assertEqual(self._next(seq, '2026-03-01'), 'FY2026-001')

    def test_choosing_never_turns_date_ranges_off_and_any_other_policy_turns_them_on(self):
        seq = self._seq('calendar')
        seq.mos_reset = 'never'
        self.assertFalse(seq.use_date_range)
        seq.mos_reset = 'month'
        self.assertTrue(seq.use_date_range)

    def test_the_numbers_of_the_documents_still_restart_each_calendar_year(self):
        """Every sequence the addons ship with keeps its yearly restart."""
        shipped = self.env['ir.sequence'].search([('code', 'like', 'mos.'), ('use_date_range', '=', True)])
        self.assertTrue(shipped)
        self.assertEqual(set(shipped.mapped('mos_reset')), {'calendar'})
