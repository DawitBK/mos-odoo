from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestApprovalDashboard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(cls.env, login='xdashadmin', groups='mos_core.group_mos_admin')
        cls.now = fields.Datetime.now()

    def _request(self, model, state, hours=None, days_ago=1):
        submitted = self.now - timedelta(days=days_ago)
        vals = {'res_model': model, 'res_id': 1, 'doc_name': f'{model} test', 'state': state, 'submitted_at': submitted}
        if hours is not None:
            vals['closed_at'] = submitted + timedelta(hours=hours)
        return self.env['mos.approval.request'].sudo().create(vals)

    def _dashboard(self, **kw):
        return self.env['mos.approval.dashboard'].with_user(self.admin).create({'date_from': fields.Date.today() - timedelta(days=60), **kw})

    def test_figures_count_each_outcome_and_average_the_approved_time(self):
        self._request('purchase.order', 'approved', hours=10)
        self._request('purchase.order', 'approved', hours=20)
        self._request('purchase.order', 'rejected', hours=5)
        self._request('purchase.order', 'returned', hours=2)
        self._request('purchase.order', 'pending')
        dash = self._dashboard(res_model='purchase.order')
        before = (dash.total, dash.approved, dash.rejected, dash.returned, dash.pending)
        self.assertEqual(before[0], before[1] + before[2] + before[3] + before[4], 'every request is in exactly one column')
        self.assertGreaterEqual(dash.approved, 2)
        self.assertGreaterEqual(dash.rejected, 1)
        only_these = self._dashboard(res_model='purchase.order')
        spans = [(r.closed_at - r.submitted_at).total_seconds() / 3600 for r in self.env['mos.approval.request'].sudo().search(only_these._domain()) if r.state == 'approved']
        self.assertAlmostEqual(only_these.avg_hours, round(sum(spans) / len(spans), 1), places=1)

    def test_a_document_kind_and_a_period_narrow_the_figures(self):
        self._request('purchase.order', 'approved', hours=4)
        self._request('mos.stock.adjustment', 'approved', hours=4)
        self._request('purchase.order', 'approved', hours=4, days_ago=400)
        everything = self._dashboard()
        orders = self._dashboard(res_model='purchase.order')
        self.assertGreater(everything.total, orders.total)
        old = self._dashboard(res_model='purchase.order', date_from=fields.Date.today() - timedelta(days=500))
        self.assertEqual(old.total, orders.total + 1, 'a longer period also counts the old request')

    def test_each_figure_opens_the_list_behind_it(self):
        self._request('purchase.order', 'pending')
        dash = self._dashboard(res_model='purchase.order')
        action = dash.action_open_pending()
        found = self.env['mos.approval.request'].sudo().search(action['domain'])
        self.assertEqual(len(found), dash.pending)
        self.assertTrue(all(r.state == 'pending' and r.res_model == 'purchase.order' for r in found))

    def test_the_trend_has_six_months_and_the_document_choices_are_named(self):
        self._request('purchase.order', 'approved', hours=3)
        dash = self._dashboard()
        self.assertEqual(str(dash.trend_html).count('class="mos-bar"'), 6, 'one row per month')
        choices = dict(self.env['mos.approval.dashboard']._doc_models())
        self.assertIn('purchase.order', choices)
        self.assertNotEqual(choices['purchase.order'], 'purchase.order', 'a name people know, not the technical one')

    def test_a_person_sees_only_what_they_may_see(self):
        self._request('purchase.order', 'pending')
        stranger = new_test_user(self.env, login='xdashstranger', groups='mos_core.group_mos_user')
        dash = self.env['mos.approval.dashboard'].with_user(stranger).create({'date_from': fields.Date.today() - timedelta(days=60)})
        self.assertEqual(dash.total, 0, 'a person who is not involved in any approval sees none')

    def test_the_overview_shows_cards_that_open_their_lists(self):
        arch = self.env['mos.overview'].with_user(self.admin).get_view(view_type='form')['arch']
        self.assertIn('mos-cards', arch)
        for method in ('action_open_trucks', 'action_open_approvals', 'action_open_late_deliveries', 'action_open_vouchers'):
            self.assertIn(method, arch)
            self.assertTrue(hasattr(self.env['mos.overview'], method), method)
        overview = self.env['mos.overview'].with_user(self.admin).create({})
        for method in ('action_open_trucks', 'action_open_inspections', 'action_open_planned_runs', 'action_open_approvals', 'action_open_silos', 'action_open_exceptions', 'action_open_lab_queue', 'action_open_vouchers', 'action_open_late_deliveries'):
            self.assertEqual(getattr(overview, method)()['type'], 'ir.actions.act_window', method)
