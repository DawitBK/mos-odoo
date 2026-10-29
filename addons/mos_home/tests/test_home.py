from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestHome(TransactionCase):
    """The home screen reads the same figures as the overview form and shows each person only what they may use."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        warehouse = cls.env['stock.warehouse'].search([], limit=1)
        cls.site = cls.env['mos.site'].create({'name': 'Mill', 'code': 'HMILL', 'warehouse_id': warehouse.id})
        cls.person = new_test_user(cls.env, login='xhome', groups='mos_core.group_mos_user')
        cls.person.mos_site_ids = [Command.set(cls.site.ids)]

    def test_the_landing_action_is_the_home_and_the_figures_stay_reachable(self):
        action = self.env['mos.overview'].action_open()
        self.assertEqual((action['type'], action['tag']), ('ir.actions.client', 'mos_home.home'))
        self.assertEqual(self.env['mos.overview'].action_open_figures()['res_model'], 'mos.overview')

    def test_the_home_carries_every_part(self):
        home = self.env['mos.overview'].with_user(self.person).get_home()
        for part in ('overview_id', 'user', 'site', 'as_of', 'today', 'month', 'attention', 'tiles', 'silos', 'grain_mix', 'gate', 'runs'):
            self.assertIn(part, home)
        self.assertEqual(home['site'], 'Mill')
        figure = {card['label']: card for card in home['today']}
        overview = self.env['mos.overview'].with_user(self.person).create({})
        self.assertEqual(figure['Trucks on site']['value'], overview.trucks_on_site, 'the home and the overview form read the same figure')

    def test_every_card_opens_something_the_overview_can_open(self):
        home = self.env['mos.overview'].with_user(self.person).get_home()
        overview = self.env['mos.overview'].browse(home['overview_id'])
        for card in home['today'] + home['attention']:
            if card['method']:
                self.assertTrue(callable(getattr(overview, card['method'], None)), card['method'])
            if card['xmlid']:
                self.assertTrue(self.env.ref(card['xmlid'], raise_if_not_found=False), card['xmlid'])

    def test_a_tile_shows_only_what_the_person_may_do(self):
        plain = new_test_user(self.env, login='xhome2', groups='mos_core.group_mos_user')
        plain.mos_site_ids = [Command.set(self.site.ids)]
        for tile in self.env['mos.overview'].with_user(plain).get_home()['tiles']:
            action = self.env.ref(tile['xmlid'])
            self.assertTrue(self.env[action.res_model].with_user(plain).has_access('create' if tile['new'] else 'read'), tile['xmlid'])
        admin_tiles = self.env['mos.overview'].get_home()['tiles']
        self.assertTrue(admin_tiles)

    def test_only_the_tiles_of_the_home_can_be_asked_for(self):
        with self.assertRaises(AccessError):
            self.env['mos.overview'].home_action('base.action_res_users')
        action = self.env['mos.overview'].home_action('mos_procurement.action_mos_purchase_request', True)
        self.assertEqual(action['view_mode'], 'form')
