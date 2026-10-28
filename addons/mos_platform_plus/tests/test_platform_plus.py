import time
from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.mos_platform_plus.models.ethiopian import from_ethiopian, to_ethiopian
from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestPlatformPlus(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        cls.admin = new_test_user(cls.env, login='xplatadmin', groups='mos_core.group_mos_admin')
        cls.gm = new_test_user(cls.env, login='xplatgm', groups='mos_approvals.group_mos_general_manager')

    # -- Ethiopian calendar ---------------------------------------------------------------------------------------
    def test_ethiopian_dates(self):
        self.assertEqual(to_ethiopian(date(2007, 9, 12)), (2000, 1, 1), 'the millennium')
        self.assertEqual(to_ethiopian(date(2026, 9, 11)), (2019, 1, 1))
        self.assertEqual(to_ethiopian(date(2026, 10, 18)), (2019, 2, 8))
        self.assertEqual(to_ethiopian(date(2027, 9, 11)), (2019, 13, 6), 'a leap year has six days in Pagume')
        self.assertEqual(to_ethiopian(date(2027, 9, 12)), (2020, 1, 1))
        for day in (date(2024, 2, 29), date(2026, 10, 18), date(2030, 1, 7), date(2031, 9, 10)):
            self.assertEqual(from_ethiopian(*to_ethiopian(day)), day, 'the conversion round-trips')
        self.assertEqual(self.env['mos.ethiopian.date'].convert('2026-10-18'), '8 Tikimt 2019')
        self.assertTrue(self.env['mos.ethiopian.date'].convert('2026-10-18', amharic=True).startswith('8 ጥቅምት'))

    # -- Role panels ------------------------------------------------------------------------------------------------
    def test_overview_counts_what_waits_for_the_person(self):
        exc = self.env['mos.exception']
        exc.create({'kind': 'gate_incident', 'summary': 'Mine', 'assignee_id': self.manager.id})
        exc.create({'kind': 'long_stay', 'summary': 'Late', 'due_date': fields.Date.today().replace(year=2020)})
        view = self.env['mos.overview'].with_user(self.manager).create({})
        self.assertGreaterEqual(view.exceptions_open, 2)
        self.assertGreaterEqual(view.exceptions_overdue, 1)
        self.assertEqual(view.exceptions_mine, 1)
        self.assertEqual(self.env['mos.overview'].with_user(self.requester).create({}).exceptions_mine, 0)

    def test_overview_does_not_fail_for_a_person_without_rights(self):
        view = self.env['mos.overview'].with_user(self.requester).create({})
        self.assertIsNotNone(view.vouchers_to_pay)
        self.assertGreaterEqual(view.amount_to_pay, 0)

    def test_overview_figures_follow_the_data(self):
        before = self.env['mos.overview'].with_user(self.manager).create({}).orders_late
        order = self._order(qty=1000.0)
        order.order_line.date_planned = fields.Datetime.now().replace(year=2020)
        self.env.flush_all()
        after = self.env['mos.overview'].with_user(self.manager).create({}).orders_late
        self.assertEqual(after, before + 1)

    # -- Access review ----------------------------------------------------------------------------------------------
    def test_role_changes_are_logged_and_cannot_be_edited(self):
        user = new_test_user(self.env, login='xnewhand', groups='mos_core.group_mos_user')
        events = self.env['mos.access.event'].sudo().search([('user_id', '=', user.id)])
        self.assertTrue(events.filtered(lambda e: e.action == 'granted'), 'creating the user logged the grant')
        lab = self.env.ref('mos_lab.group_mos_lab_analyst')
        user.with_user(self.admin).sudo().write({'group_ids': [Command.link(lab.id)]})
        granted = self.env['mos.access.event'].sudo().search([('user_id', '=', user.id), ('group_id', '=', lab.id), ('action', '=', 'granted')])
        self.assertEqual(len(granted), 1)
        user.sudo().write({'group_ids': [Command.unlink(lab.id)]})
        revoked = self.env['mos.access.event'].sudo().search([('user_id', '=', user.id), ('group_id', '=', lab.id), ('action', '=', 'revoked')])
        self.assertEqual(len(revoked), 1)
        with self.assertRaises(UserError):
            revoked.sudo().write({'action': 'granted'})
        with self.assertRaises(UserError):
            revoked.sudo().unlink()

    def test_access_review_lists_roles_sites_and_exports(self):
        review = self.env['mos.access.review'].with_user(self.gm).create({})
        review.action_build()
        logins = review.line_ids.mapped('login')
        self.assertIn('xbuyer', logins)
        buyer_line = review.line_ids.filtered(lambda l: l.login == 'xbuyer')[:1]
        self.assertIn('MILLPP', buyer_line.sites)
        self.assertTrue(review.file and review.filename.endswith('.csv'))
        with self.assertRaises(UserError):
            self.env['mos.access.review'].with_user(self.buyer).create({}).action_build()

    def test_review_shows_a_user_with_no_site(self):
        user = new_test_user(self.env, login='xnosite', groups='mos_core.group_mos_user')
        user.mos_site_ids = [Command.clear()]
        review = self.env['mos.access.review'].with_user(self.admin).create({})
        review.action_build()
        self.assertIn('no site', review.line_ids.filtered(lambda l: l.login == 'xnosite')[:1].sites)

    # -- Working site -----------------------------------------------------------------------------------------------
    def test_working_site(self):
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        other = self.env['mos.site'].create({'name': 'Second mill', 'code': 'MILL2', 'warehouse_id': warehouse.id})
        user = new_test_user(self.env, login='xtwosites', groups='mos_core.group_mos_user')
        user.mos_site_ids = [Command.set((self.site | other).ids)]
        self.assertIn(user._mos_current_site(), self.site | other, 'one of their sites until one is chosen')
        user.mos_active_site_id = other
        self.assertEqual(user._mos_current_site(), other)
        user.mos_site_ids = [Command.unlink(other.id)]
        self.assertFalse(user.mos_active_site_id, 'losing the site clears the choice')
        with self.assertRaises(ValidationError):
            user.mos_active_site_id = other

    def test_a_person_can_choose_their_own_working_site(self):
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        other = self.env['mos.site'].create({'name': 'Third mill', 'code': 'MILL3', 'warehouse_id': warehouse.id})
        user = new_test_user(self.env, login='xchooser', groups='mos_core.group_mos_user')
        user.mos_site_ids = [Command.set((self.site | other).ids)]
        user.with_user(user).write({'mos_active_site_id': other.id})
        self.assertEqual(user.mos_active_site_id, other)

    # -- Identity provider groups -----------------------------------------------------------------------------------
    def test_idp_groups_give_and_take_back_roles(self):
        lab = self.env.ref('mos_lab.group_mos_lab_analyst')
        admin = self.env.ref('mos_core.group_mos_admin')
        maps = self.env['mos.idp.group.map']
        maps.create({'idp_group': 'mill-lab', 'group_id': lab.id, 'site_ids': [Command.set(self.site.ids)]})
        maps.create({'idp_group': 'mill-root', 'group_id': admin.id})
        user = new_test_user(self.env, login='xidp', groups='mos_core.group_mos_user')
        user.mos_site_ids = [Command.clear()]
        handbuilt = self.env.ref('mos_inbound.group_mos_storekeeper')
        user.sudo().write({'group_ids': [Command.link(handbuilt.id)]})

        self.assertTrue(user._mos_apply_idp_claims({'groups': ['mill-lab', 'mill-root']}))
        self.assertIn(lab, user.group_ids)
        self.assertIn(self.site, user.mos_site_ids)
        self.assertNotIn(admin, user.group_ids, 'the administrator role is never given by a mapping')

        user._mos_apply_idp_claims({'groups': ['unrelated']})
        self.assertNotIn(lab, user.group_ids, 'the provider no longer reports the group')
        self.assertNotIn(self.site, user.mos_site_ids)
        self.assertIn(handbuilt, user.group_ids, 'a role given by hand stays')
        self.assertTrue(self.env['mos.access.event'].sudo().search([('user_id', '=', user.id), ('group_id', '=', lab.id), ('action', '=', 'revoked')]))

    def test_idp_without_a_group_claim_changes_nothing(self):
        lab = self.env.ref('mos_lab.group_mos_lab_analyst')
        user = new_test_user(self.env, login='xidpnone', groups='mos_core.group_mos_user')
        user.sudo().write({'group_ids': [Command.link(lab.id)]})
        self.assertFalse(user._mos_apply_idp_claims({'email': 'a@b.c'}))
        self.assertIn(lab, user.group_ids)

    def test_idp_group_claim_name_is_configurable_and_accepts_a_string(self):
        lab = self.env.ref('mos_lab.group_mos_lab_analyst')
        self.env['ir.config_parameter'].sudo().set_str('mos_auth_oidc.groups_claim', 'roles')
        self.env['mos.idp.group.map'].create({'idp_group': 'lab', 'group_id': lab.id})
        user = new_test_user(self.env, login='xidpcsv', groups='mos_core.group_mos_user')
        user._mos_apply_idp_claims({'roles': 'other, lab'})
        self.assertIn(lab, user.group_ids)

    # -- Accounts created at the first sign-in, when the deployment lets the identity provider decide -------------------
    def _idp_roles_setup(self, create='1'):
        params = self.env['ir.config_parameter'].sudo()
        params.set_str('mos_auth_oidc.groups_claim', 'app_roles')
        params.set_str('mos_platform_plus.idp_create_accounts', create)
        lab = self.env.ref('mos_lab.group_mos_lab_analyst')
        self.env['mos.idp.group.map'].create({'idp_group': 'analyst', 'group_id': lab.id, 'site_ids': [Command.set(self.site.ids)]})
        return lab

    def _identity(self, email, roles, **extra):
        return {'sub': 'sub-' + email, 'email': email, 'email_verified': True, 'app_roles': roles, **extra}

    def test_idp_account_creation_is_off_unless_the_deployment_turns_it_on(self):
        self._idp_roles_setup(create='0')
        self.assertFalse(self.env['res.users']._mos_provision_from_idp(self._identity('xnew1@example.com', ['analyst'])))
        self.assertFalse(self.env['res.users'].sudo().search([('login', '=', 'xnew1@example.com')]))
        self.env['ir.config_parameter'].sudo().search([('key', '=', 'mos_platform_plus.idp_create_accounts')]).unlink()
        self.assertFalse(self.env['res.users']._mos_provision_from_idp(self._identity('xnew2@example.com', ['analyst'])), 'no setting means off')

    def test_idp_account_is_created_for_a_person_with_a_mapped_role_and_gets_it_with_its_sites(self):
        lab = self._idp_roles_setup()
        user = self.env['res.users']._mos_provision_from_idp(self._identity('xnew3@example.com', ['analyst'], given_name='Abebe', family_name='Kebede'))
        self.assertTrue(user)
        self.assertEqual((user.login, user.email, user.name), ('xnew3@example.com', 'xnew3@example.com', 'Abebe Kebede'))
        self.assertTrue(user.active)
        user._mos_apply_idp_claims(self._identity('xnew3@example.com', ['analyst']))
        self.assertIn(lab, user.group_ids)
        self.assertIn(self.site, user.mos_site_ids)
        self.assertFalse(user.password, 'single sign-on only: no password is set')
        event = self.env['mos.audit.event'].sudo().search([('action', '=', 'idp_account_created'), ('res_id', '=', user.id)])
        self.assertTrue(event)

    def test_idp_account_creation_works_on_a_sign_in_route_where_there_is_no_user_and_no_company(self):
        # The sign-in callback runs on a public route whose environment has no user at all (uid None), so no current company either. Creating the
        # account the plain way fails there on a missing company, and then on the avatar write that needs a user (found on the live system).
        from odoo.api import Environment

        lab = self._idp_roles_setup()
        route = Environment(self.env.cr, None, {})
        self.assertFalse(route.company, 'the route environment really has no company')
        user = route['res.users']._mos_provision_from_idp(self._identity('xroute@example.com', ['analyst'], given_name='Quinn', family_name='Gate'))
        self.assertTrue(user)
        self.assertEqual(user.company_ids, user.company_id)
        self.assertTrue(user.company_id)
        user._mos_apply_idp_claims(self._identity('xroute@example.com', ['analyst']))
        self.assertIn(lab, user.sudo().group_ids)
        event = self.env['mos.audit.event'].sudo().search([('action', '=', 'idp_account_created'), ('res_id', '=', user.id)])
        self.assertEqual(event.user_id, user, 'the person is the one who acted')

    def test_idp_account_is_never_created_for_the_administrator_role_alone_or_for_an_unmapped_role(self):
        self._idp_roles_setup()
        admin = self.env.ref('mos_core.group_mos_admin')
        self.env['mos.idp.group.map'].create({'idp_group': 'admin', 'group_id': admin.id})
        users = self.env['res.users']
        self.assertFalse(users._mos_provision_from_idp(self._identity('xnew4@example.com', ['admin'])), 'a mapping never gives the administrator role, so there is nothing to create')
        self.assertFalse(users._mos_provision_from_idp(self._identity('xnew5@example.com', ['wizard'])))
        self.assertFalse(users._mos_provision_from_idp(self._identity('xnew6@example.com', [])))
        self.assertFalse(users._mos_provision_from_idp({'sub': 's', 'email': 'xnew7@example.com', 'email_verified': True}), 'no claim at all')
        self.assertFalse(users.sudo().search([('login', 'in', ['xnew4@example.com', 'xnew5@example.com', 'xnew6@example.com', 'xnew7@example.com'])]))

    def test_idp_account_creation_reads_a_comma_separated_claim_and_uses_the_address_when_there_is_no_name(self):
        self._idp_roles_setup()
        user = self.env['res.users']._mos_provision_from_idp({'sub': 's', 'email': ' XNew8@Example.com ', 'email_verified': True, 'app_roles': 'wizard, analyst'})
        self.assertEqual((user.login, user.name), ('xnew8@example.com', 'xnew8@example.com'))

    # -- Stock reports ----------------------------------------------------------------------------------------------
    def test_stock_overview_receipt_valuation_and_balance(self):
        order = self._order(qty=40000.0)
        self._receive(order, 'PLT-001')
        self.env.flush_all()
        positions = self.env['mos.stock.position'].search([('product_id', '=', self.wheat.id)])
        on_hand = sum(positions.mapped('on_hand'))
        self.assertGreater(on_hand, 0)
        rows = self.env['mos.receipt.valuation'].search([('order_id', '=', order.id)])
        self.assertTrue(rows)
        insp = self.env['mos.lab.inspection'].search([('visit_id.purchase_id', '=', order.id)], limit=1)
        self.assertAlmostEqual(sum(rows.mapped('payable')), insp.payable_qty, places=1)
        self.assertAlmostEqual(sum(rows.mapped('value')), insp.payable_qty * 20.0 * (1 + insp.price_adjust_pct / 100.0), places=1)
        self.assertAlmostEqual(rows[0].unit_cost, rows[0].value / rows[0].qty, places=4)
        balance = self.env['mos.stock.balance'].with_user(self.requester).create({'date_from': fields.Date.today().replace(day=1), 'date_to': fields.Date.today()})
        balance.action_build()
        line = balance.line_ids.filtered(lambda l: l.product_id == self.wheat)
        self.assertEqual(len(line), 1)
        self.assertAlmostEqual(line.closing, line.opening + line.received - line.issued + line.adjusted, places=3)
        self.assertAlmostEqual(line.available + line.quarantine + line.blocked, line.closing, places=1, msg='the status columns add up to on-hand')
        self.assertAlmostEqual(line.closing, on_hand, places=1)
        with self.assertRaises(UserError):
            self.env['mos.stock.balance'].create({'date_from': '2026-10-02', 'date_to': '2026-10-01'}).action_build()

    # -- Close checklist --------------------------------------------------------------------------------------------
    def test_close_checklist_blocks_sign_off_while_items_are_open(self):
        checklist = self.env['mos.close.checklist'].with_user(self.gm).create({})
        checklist.action_run()
        self.assertGreaterEqual(len(checklist.line_ids), 5)
        self.env['mos.exception'].create({'kind': 'lock_missing', 'severity': 'high', 'summary': 'The September period is not closed'})
        checklist.action_run()
        line = checklist.line_ids.filtered(lambda l: l.res_model == 'mos.exception')
        self.assertGreaterEqual(line.count, 1)
        self.assertFalse(checklist.ready)
        with self.assertRaises(UserError):
            checklist.action_sign_off()
        action = line.action_open()
        self.assertEqual(action['res_model'], 'mos.exception')

    def test_close_checklist_counts_a_pending_stock_adjustment(self):
        reason = self.env['mos.reason'].create({'domain': 'adjustment', 'code': 'PLTX', 'name': 'Platform test'})
        location = self.env['stock.warehouse'].search([], limit=1).lot_stock_id
        adj = self.env['mos.stock.adjustment'].create({'site_id': self.site.id, 'location_id': location.id, 'reason_id': reason.id})
        checklist = self.env['mos.close.checklist'].create({})
        checklist.action_run()
        line = checklist.line_ids.filtered(lambda l: l.res_model == 'mos.stock.adjustment')
        self.assertGreaterEqual(line.count, 1)
        self.assertEqual(adj.state, 'draft')

    # -- Notifications ----------------------------------------------------------------------------------------------
    def test_exception_digest_is_off_until_enabled_and_lists_each_persons_items(self):
        digest = self.env['mos.exception.digest']
        self.env['ir.config_parameter'].sudo().set_str('mos_platform_plus.digest_enabled', 'False')
        self.assertEqual(digest._cron_send(), 0)
        self.manager.email = 'manager@example.test'
        self.env['mos.exception'].create({'kind': 'gate_incident', 'summary': 'Seal broken', 'assignee_id': self.manager.id, 'due_date': '2020-01-01'})
        self.env['mos.exception'].create({'kind': 'long_stay', 'summary': 'Truck 3 days on site', 'assignee_id': self.manager.id})
        mails = digest._send()
        mail = mails.filtered(lambda m: m.email_to == 'manager@example.test')
        self.assertEqual(len(mail), 1, 'one mail per person, not per exception')
        self.assertIn('OVERDUE', mail.body_html)
        self.assertIn('Seal broken', mail.body_html)
        self.assertIn('Truck 3 days on site', mail.body_html)
        self.env['ir.config_parameter'].sudo().set_str('mos_platform_plus.digest_enabled', 'True')
        self.assertGreaterEqual(digest._cron_send(), 1)

    def test_balance_separates_adjustments_and_keeps_a_history(self):
        order = self._order(qty=40000.0)
        self._receive(order, 'PLT-002')
        location = self.env['stock.warehouse'].search([], limit=1).lot_stock_id
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': self.wheat.id, 'location_id': location.id, 'inventory_quantity': 1000.0})
        quant.action_apply_inventory()  # a count correction down to 1,000 kg
        self.env.flush_all()
        balance = self.env['mos.stock.balance'].create({'date_from': fields.Date.today().replace(day=1), 'date_to': fields.Date.today()})
        balance.action_build()
        line = balance.line_ids.filtered(lambda l: l.product_id == self.wheat)
        self.assertLess(line.adjusted, 0, 'the correction is an adjustment, not an issue')
        self.assertAlmostEqual(line.closing, line.opening + line.received - line.issued + line.adjusted, places=3)
        self.assertAlmostEqual(line.closing, 1000.0, places=1)
        history = self.env['mos.stock.history'].search([('product_id', '=', self.wheat.id)], order='date, id')
        self.assertEqual({'received', 'adjusted'}, set(history.mapped('kind')))
        self.assertAlmostEqual(history[-1].balance_after, 1000.0, places=1, msg='the running balance ends where the stock stands')
        self.assertEqual(line.action_history()['res_model'], 'mos.stock.history')

    # -- Soft-closed periods ------------------------------------------------------------------------------------------
    def test_soft_closed_period_needs_a_finance_manager_to_post(self):
        fin = new_test_user(self.env, login='xplatfin', groups='mos_approvals.group_mos_finance_manager')
        lock = self.env['mos.period.lock'].sudo().search([('area', '=', 'payables')], limit=1) or self.env['mos.period.lock'].sudo().create({'area': 'payables'})
        yesterday = fields.Date.today() - timedelta(days=1)
        with self.assertRaises(UserError):
            lock.with_user(self.apclerk).set_soft_lock(yesterday)  # not a finance manager
        lock.with_user(fin).set_soft_lock(yesterday, 'month-end review')
        self.assertEqual(lock.soft_lock_date, yesterday)
        locks = self.env['mos.period.lock']
        with self.assertRaises(UserError):
            locks.with_user(self.apclerk).check('payables', yesterday)  # the clerk can no longer post into it
        locks.with_user(fin).check('payables', yesterday)  # the finance manager still can
        locks.with_user(self.apclerk).check('payables', fields.Date.today())  # after the soft-close date is open
        lock.sudo().write({'lock_date': yesterday})
        with self.assertRaises(UserError):
            locks.with_user(fin).check('payables', yesterday)  # once really closed, nobody can
        with self.assertRaises(UserError):
            lock.with_user(fin).set_soft_lock(yesterday - timedelta(days=5))  # cannot be earlier than the closing date
        lock.with_user(fin).set_soft_lock(False, 'reopened')
        self.assertFalse(lock.soft_lock_date)

    # -- Amharic pack ----------------------------------------------------------------------------------------------
    def test_the_amharic_pack_translates_menus_and_labels(self):
        self.env['res.lang']._activate_lang('am_ET')
        self.env['ir.module.module']._load_module_terms(['mos_core', 'mos_gate'], ['am_ET'], overwrite=True)
        menu = self.env.ref('mos_core.menu_mos_sites')
        self.assertEqual(menu.with_context(lang='am_ET').name, 'ቦታዎች')
        self.assertEqual(menu.with_context(lang='en_US').name, 'Sites')
        field = self.env['ir.model.fields']._get('mos.gate.visit', 'plate')
        self.assertTrue(field.with_context(lang='am_ET').field_description)
        arrived = dict(self.env['mos.gate.visit'].with_context(lang='am_ET').fields_get(['state'])['state']['selection'])['arrived']
        self.assertEqual(arrived, 'ደርሷል')


@tagged('post_install', '-at_install')
class TestExpiredSession(HttpCase):
    def test_the_idle_limit_is_odoos_own_and_set_to_eight_hours(self):
        from odoo.http.session import get_session_max_inactivity

        self.assertEqual(get_session_max_inactivity(self.env), 8 * 3600)

    def test_a_request_from_an_expired_session_ends_at_the_sign_in_page(self):
        """Every redirect is followed: an expired session once answered 500, and a first repair made the sign-in page redirect to itself until the browser gave up (HTTP 431)."""
        from odoo.http.session import session_store

        session = self.authenticate('admin', 'admin')
        session['deletion_time'] = time.time() - 10
        session_store().save(session)
        response = self.url_open('/odoo', allow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.history), 6, 'a short chain of redirects, not a loop')
        self.assertLess(len(response.url), 400)
