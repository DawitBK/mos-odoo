from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestControls(TransactionCase):
    """Period locks, date windows, the exceptions queue and global search."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLC', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.finance = user('cfinance', 'mos_approvals.group_mos_finance_manager')
        cls.gm = user('cgm', 'mos_approvals.group_mos_general_manager')
        cls.clerk = user('cclerk', 'mos_core.group_mos_user')
        cls.store = user('cstore', 'mos_inbound.group_mos_storekeeper')
        cls.product = env['product.product'].create({'name': 'Bran', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 5.0})
        cls.reason = env['mos.reason'].create({'domain': 'adjustment', 'code': 'CTL', 'name': 'Control test'})
        cls.today = fields.Date.today()

    def _adjustment(self, date):
        return self.env['mos.stock.adjustment'].with_user(self.store).create(
            {
                'site_id': self.site.id,
                'location_id': self.site.warehouse_id.lot_stock_id.id,
                'reason_id': self.reason.id,
                'date': date,
                'line_ids': [Command.create({'product_id': self.product.id, 'new_qty': 10.0})],
            }
        )

    # -- period locks ---------------------------------------------------------------------------------------
    def test_closed_period_refuses_documents(self):
        lock = self.env['mos.period.lock'].lock_for('inventory')
        self.assertTrue(lock, 'a lock row exists for each area')
        yesterday = self.today - timedelta(days=1)
        with self.assertRaises(UserError):
            lock.with_user(self.clerk).set_lock(yesterday)  # not finance
        lock.with_user(self.finance).set_lock(yesterday)
        self.assertEqual(lock.lock_date, yesterday)
        with self.assertRaises(UserError):
            self._adjustment(yesterday)
        adj = self._adjustment(self.today)
        with self.assertRaises(UserError):
            adj.write({'date': yesterday - timedelta(days=3)})  # redating into the closed period
        lock.with_user(self.finance).set_lock(self.today - timedelta(days=1))  # same date is fine

    def test_only_the_general_manager_reopens_a_period(self):
        lock = self.env['mos.period.lock'].lock_for('payables')
        lock.with_user(self.finance).set_lock(self.today - timedelta(days=10))
        with self.assertRaises(UserError):
            lock.with_user(self.finance).set_lock(self.today - timedelta(days=20), 'Correction')
        with self.assertRaises(UserError):
            lock.with_user(self.gm).set_lock(self.today - timedelta(days=20))  # no reason
        lock.with_user(self.gm).set_lock(self.today - timedelta(days=20), 'Missed supplier invoice')
        self.assertEqual(lock.lock_date, self.today - timedelta(days=20))
        self.assertEqual(lock.change_reason, 'Missed supplier invoice')
        with self.assertRaises(UserError):
            lock.with_user(self.finance).set_lock(self.today + timedelta(days=3))  # future
        events = self.env['mos.audit.event'].search([('action', '=', 'period_lock_changed')])
        self.assertGreaterEqual(len(events), 2)

    # -- date windows ------------------------------------------------------------------------------------------
    def test_backdating_window_and_sanity(self):
        self.env['mos.date.policy'].create({'area': 'inventory', 'max_back_days': 3, 'max_forward_days': 1})
        self.assertTrue(self._adjustment(self.today - timedelta(days=3)))
        with self.assertRaises(UserError):
            self._adjustment(self.today - timedelta(days=4))
        with self.assertRaises(UserError):
            self._adjustment(self.today + timedelta(days=2))
        self.assertTrue(self._adjustment(self.today + timedelta(days=1)))

    def test_implausible_year_is_refused(self):
        with self.assertRaises(UserError):
            self.env['mos.period.lock'].check('inventory', fields.Date.to_date('1970-01-01'))
        with self.assertRaises(UserError):
            self.env['mos.period.lock'].check('inventory', fields.Date.to_date('2999-01-01'))

    # -- exceptions -------------------------------------------------------------------------------------------
    def test_scan_opens_and_clears_exceptions(self):
        incident = self.env['mos.gate.incident'].create({'site_id': self.site.id, 'kind': 'safety', 'description': 'Spill near the bridge'})
        opened, _closed = self.env['mos.exception']._cron_scan()
        self.assertGreaterEqual(opened, 1)
        exc = self.env['mos.exception'].search([('kind', '=', 'gate_incident'), ('res_id', '=', incident.id)])
        self.assertEqual(len(exc), 1)
        self.assertEqual(exc.state, 'open')
        self.env['mos.exception']._cron_scan()
        self.assertEqual(self.env['mos.exception'].search_count([('kind', '=', 'gate_incident'), ('res_id', '=', incident.id)]), 1, 'no duplicate for the same cause')
        incident.resolution = 'Cleaned'
        incident.action_close()
        self.env['mos.exception']._cron_scan()
        self.assertEqual(exc.state, 'resolved')
        self.assertIn('Cleared by itself', exc.resolution)

    def test_exception_workflow_rules(self):
        incident = self.env['mos.gate.incident'].create({'site_id': self.site.id, 'kind': 'overload', 'description': 'x'})
        exc = self.env['mos.exception'].raise_for('gate_incident', incident, 'Manual', severity='high', due_days=-1)
        self.assertTrue(exc.overdue)
        self.assertIn(exc, self.env['mos.exception'].search([('overdue', '=', True)]))
        self.assertNotIn(exc, self.env['mos.exception'].search([('overdue', '=', False)]))
        exc.with_user(self.clerk).action_assign()
        self.assertEqual(exc.state, 'assigned')
        with self.assertRaises(UserError):
            exc.with_user(self.clerk).action_dismiss('Not a problem')  # high severity: administrators only
        with self.assertRaises(UserError):
            exc.with_user(self.clerk).action_resolve('')
        exc.with_user(self.clerk).action_resolve('Spoke to the driver')
        self.assertEqual(exc.state, 'resolved')
        with self.assertRaises(UserError):
            exc.action_resolve('again')

    # -- search -------------------------------------------------------------------------------------------------
    def test_global_search_finds_across_models_and_respects_rights(self):
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'ZX-9981', 'driver_name': 'Tadesse'})
        search = self.env['mos.search'].with_user(self.store).create({'query': 'zx-9981'})
        search.action_search()
        hits = search.result_ids
        self.assertEqual(hits.mapped('res_id'), [visit.id])
        self.assertEqual(hits.res_model, 'mos.gate.visit')
        action = hits.action_open()
        self.assertEqual((action['res_model'], action['res_id']), ('mos.gate.visit', visit.id))
        search.query = 'x'
        search.action_search()
        self.assertFalse(search.result_ids, 'a one-letter query finds nothing')
        outsider = new_test_user(self.env, login='coutsider', groups='mos_core.group_mos_user')  # no site assigned
        blind = self.env['mos.search'].with_user(outsider).create({'query': 'ZX-9981'})
        blind.action_search()
        self.assertFalse(blind.result_ids, 'a visit at a site the person is not assigned to stays hidden')
