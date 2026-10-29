from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestMill(TransactionCase):
    """Silos, milling runs and the overview: the stock ledger is the single source of every figure."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILL', 'warehouse_id': warehouse.id})
        kg = env.ref('uom.product_uom_kgm')
        flour_cat = env['product.category'].create({'name': 'Flour'})

        def product(name, categ=None):
            return env['product.product'].create(
                {'name': name, 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'categ_id': (categ or env.ref('product.product_category_goods')).id}
            )

        cls.wheat = product('Wheat')
        cls.flour = product('Flour 72', flour_cat)
        cls.bran = product('Bran')
        cls.silo = env['mos.silo'].create({'code': 'S01', 'name': 'Silo 1', 'site_id': cls.site.id, 'product_id': cls.wheat.id, 'capacity_kg': 100000})
        env['stock.quant'].sudo()._update_available_quantity(cls.wheat, cls.silo.location_id, 60000)

    def _run(self, wheat=50000, flour=37000, bran=11000):
        return self.env['mos.milling.run'].create(
            {
                'site_id': self.site.id,
                'input_ids': [Command.create({'silo_id': self.silo.id, 'product_id': self.wheat.id, 'qty_kg': wheat})],
                'output_ids': [
                    Command.create({'product_id': self.flour.id, 'kind': 'flour', 'qty_kg': flour}),
                    Command.create({'product_id': self.bran.id, 'kind': 'bran', 'qty_kg': bran}),
                ],
            }
        )

    def test_silo_level_comes_from_the_ledger(self):
        silo = self.silo.fresh()
        self.assertEqual(silo.level_kg, 60000)
        self.assertEqual(silo.free_kg, 40000)
        self.assertEqual(round(silo.fill_pct), 60)
        self.assertEqual(silo.status, 'ok')
        self.assertTrue(silo.location_id.complete_name.endswith('Silo S01'))

    def test_milling_run_moves_stock_and_computes_extraction(self):
        run = self._run()
        self.assertEqual(run.extraction_pct, 74.0)
        self.assertEqual(run.loss_kg, 2000)
        run.action_complete()
        self.assertEqual(run.state, 'done')
        self.assertEqual(self.silo.fresh().level_kg, 10000)
        self.assertEqual(self.flour.with_context(location=self.site.warehouse_id.lot_stock_id.id).qty_available, 37000)
        self.assertRegex(run.name, r'^MR-\d{4}-\d{5}$')

    def test_completed_run_is_final(self):
        run = self._run()
        run.action_complete()
        with self.assertRaises(UserError):
            run.write({'shift': 'night'})
        with self.assertRaises(UserError):
            run.input_ids[0].write({'qty_kg': 1})
        with self.assertRaises(UserError):
            run.unlink()
        with self.assertRaises(UserError):
            run.action_complete()

    def test_run_cannot_draw_more_than_the_silo_holds(self):
        run = self._run(wheat=70000, flour=50000, bran=15000)
        with self.assertRaises(UserError):
            run.action_complete()
        self.assertEqual(run.state, 'draft')
        self.assertEqual(self.silo.fresh().level_kg, 60000)

    def test_products_cannot_outweigh_the_wheat(self):
        with self.assertRaises(UserError):
            self._run(wheat=10000, flour=9000, bran=2000).action_complete()

    def test_overview_reads_live_figures(self):
        self._run().action_complete()
        overview = self.env['mos.overview'].create({})
        self.assertEqual(overview.silo_capacity_kg, 100000)
        self.assertEqual(overview.silo_level_kg, 10000)
        self.assertEqual(overview.wheat_today_kg, 50000)
        self.assertEqual(overview.flour_today_kg, 37000)
        self.assertEqual(overview.extraction_30d, 74.0)
        self.assertEqual(overview.flour_stock_kg, 37000)
        action = self.env['mos.overview'].action_open_figures()
        self.assertEqual(action['res_model'], 'mos.overview')
        self.assertTrue(action['res_id'])

    def test_the_overview_shows_the_operation_unit_the_person_works_at(self):
        from odoo.tests.common import new_test_user

        other = self.env['mos.site'].create({'name': 'Depot', 'code': 'DEPOT', 'warehouse_id': self.env['stock.warehouse'].search([], limit=1).id})
        self.env['mos.silo'].create({'code': 'D01', 'name': 'Depot silo', 'site_id': other.id, 'product_id': self.wheat.id, 'capacity_kg': 50000})
        person = new_test_user(self.env, login='xoverview', groups='mos_core.group_mos_user')
        person.mos_site_ids = [Command.set(self.site.ids)]
        mine = self.env['mos.overview'].with_user(person).create({})
        self.assertEqual(mine.silo_capacity_kg, 100000, 'only the silos of their own unit')
        self.assertIn('Mill', mine.title)

    def test_the_levels_of_a_silo_are_kept(self):
        self.silo.write({'min_level_kg': 20000, 'max_level_kg': 70000, 'safety_stock_kg': 5000})
        self.silo.fresh()
        self.assertEqual(self.silo.free_kg, 10000, 'a load may take it up to the maximum, 70,000, not to the capacity')
        self.assertEqual(self.silo.status, 'ok')
        with self.assertRaises(ValidationError):
            self.silo.max_level_kg = 120000  # above the capacity
        with self.assertRaises(ValidationError):
            self.silo.safety_stock_kg = 25000  # above the minimum level
        self.silo.write({'min_level_kg': 80000, 'max_level_kg': 90000})
        self.silo.fresh()
        self.assertEqual(self.silo.status, 'low', 'below the minimum level the silo is low, whatever the percentage')

    def test_a_milling_run_cannot_draw_a_silo_below_its_safety_stock(self):
        self.silo.write({'safety_stock_kg': 15000})
        with self.assertRaisesRegex(UserError, 'safety stock'):
            self._run(wheat=50000).action_complete()  # 60,000 in the silo: 10,000 would be left
        self.silo.safety_stock_kg = 10000
        self._run(wheat=50000).action_complete()
