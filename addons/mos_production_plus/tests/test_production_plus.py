from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestProductionPlus(TransactionCase):
    """Recipes and grist, work orders, silo dips against the ledger, treatment holds."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.warehouse = warehouse
        cls.stock = warehouse.lot_stock_id
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLP2', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.sup = user('ppsup', 'mos_manufacturing.group_mos_production_supervisor')
        cls.sup2 = user('ppsup2', 'mos_manufacturing.group_mos_production_supervisor')
        cls.inv = user('ppinv', 'mos_inventory.group_mos_inventory_manager')
        cls.store = user('ppstore', 'mos_inbound.group_mos_storekeeper')
        kg = env.ref('uom.product_uom_kgm')

        def product(name, cost):
            return env['product.product'].create({'name': name, 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': cost})

        cls.durum = product('Durum PP', 22.0)
        cls.bread = product('Bread wheat PP', 19.0)
        cls.flour = product('Semolina PP', 0.0)
        cls.bran = product('Bran PP', 0.0)
        cls.silo_d = env['mos.silo'].create({'name': 'Silo D', 'code': 'PD', 'site_id': cls.site.id, 'capacity_kg': 300000, 'product_id': cls.durum.id})
        cls.silo_b = env['mos.silo'].create({'name': 'Silo B', 'code': 'PB', 'site_id': cls.site.id, 'capacity_kg': 300000, 'product_id': cls.bread.id})
        cls._set_stock(cls.durum, cls.silo_d.location_id, 100000.0)
        cls._set_stock(cls.bread, cls.silo_b.location_id, 100000.0)

    @classmethod
    def _set_stock(cls, product, location, qty):
        quant = cls.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id, 'location_id': location.id, 'inventory_quantity': qty})
        quant.action_apply_inventory()

    def _recipe(self, version=None, durum=70.0, bread=30.0, approve=True):
        recipe = self.env['mos.recipe'].with_user(self.sup).create(
            {
                'product_id': self.flour.id,
                'version': version or 1,
                'target_extraction_pct': 72.0,
                'line_ids': [Command.create({'wheat_id': self.durum.id, 'share_pct': durum}), Command.create({'wheat_id': self.bread.id, 'share_pct': bread})],
            }
        )
        if approve:
            recipe.with_user(self.sup2).action_approve()
        return recipe

    # -- recipes --------------------------------------------------------------------------------------------------
    def test_recipe_must_total_100_and_be_approved_by_someone_else(self):
        recipe = self._recipe(approve=False)
        with self.assertRaises(UserError):
            recipe.with_user(self.sup).action_approve()  # its author
        bad = self.env['mos.recipe'].with_user(self.sup).create({'product_id': self.flour.id, 'version': 5, 'line_ids': [Command.create({'wheat_id': self.durum.id, 'share_pct': 60.0})]})
        with self.assertRaises(UserError):
            bad.with_user(self.sup2).action_approve()  # 60 %
        recipe.with_user(self.sup2).action_approve()
        self.assertEqual(recipe.state, 'approved')
        with self.assertRaises(UserError):
            recipe.line_ids[:1].write({'share_pct': 10})  # frozen
        v2 = recipe.action_new_version()
        self.assertEqual((v2.version, len(v2.line_ids), v2.state), (bad.version + 1, 2, 'draft'), 'a new version follows the highest one')
        v2.with_user(self.sup2).action_approve()
        self.assertEqual(recipe.state, 'retired')
        self.assertEqual(self.env['mos.recipe'].in_force(self.flour), v2)
        with self.assertRaises(ValidationError):
            self.env['mos.recipe.line'].create({'recipe_id': bad.id, 'wheat_id': self.bread.id, 'share_pct': 0})

    # -- grist check on a run ------------------------------------------------------------------------------------------
    def _run(self, recipe, durum_kg=14000.0, bread_kg=6000.0, flour=14400.0):
        return self.env['mos.milling.run'].with_user(self.sup).create(
            {
                'site_id': self.site.id,
                'run_date': fields.Date.today(),
                'recipe_id': recipe.id,
                'input_ids': [
                    Command.create({'silo_id': self.silo_d.id, 'product_id': self.durum.id, 'qty_kg': durum_kg}),
                    Command.create({'silo_id': self.silo_b.id, 'product_id': self.bread.id, 'qty_kg': bread_kg}),
                ],
                'output_ids': [Command.create({'product_id': self.flour.id, 'kind': 'flour', 'qty_kg': flour}), Command.create({'product_id': self.bran.id, 'kind': 'bran', 'qty_kg': 4000.0})],
            }
        )

    def test_a_run_off_the_recipe_is_flagged_or_refused(self):
        recipe = self._recipe()
        good = self._run(recipe)  # 70 / 30, 72 % extraction
        self.assertEqual(good._mos_grist_problems(), [])
        good.with_user(self.sup).action_complete()
        self.assertFalse(self.env['mos.exception'].search([('kind', '=', 'off_recipe'), ('res_id', '=', good.id)]))
        off = self._run(recipe, durum_kg=10000.0, bread_kg=10000.0, flour=14400.0)  # 50 / 50
        problems = off._mos_grist_problems()
        self.assertEqual(len(problems), 2)
        off.with_user(self.sup).action_complete()
        self.assertTrue(self.env['mos.exception'].search([('kind', '=', 'off_recipe'), ('res_id', '=', off.id)]), 'completing off recipe opens an exception')
        self.env['ir.config_parameter'].sudo().set_str('mos_production_plus.enforce_grist', 'True')
        strict = self._run(recipe, durum_kg=10000.0, bread_kg=10000.0)
        with self.assertRaises(UserError):
            strict.with_user(self.sup).action_complete()

    def test_extraction_outside_the_recipe_is_a_problem(self):
        recipe = self._recipe()
        low = self._run(recipe, flour=12000.0)  # 60 %
        self.assertTrue(any('extraction' in p for p in low._mos_grist_problems()))

    # -- work orders ----------------------------------------------------------------------------------------------------
    def test_work_order_plans_the_wheat_starts_a_run_and_finishes(self):
        recipe = self._recipe()
        order = self.env['mos.work.order'].with_user(self.sup).create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 20000.0})
        with self.assertRaises(UserError):
            order.action_start()  # not planned
        order.action_plan()
        lines = {l.wheat_id: l for l in order.line_ids}
        self.assertEqual((lines[self.durum].qty_kg, lines[self.durum].silo_id), (14000.0, self.silo_d))
        self.assertEqual(lines[self.bread].qty_kg, 6000.0)
        order.action_start()
        run = order.run_id
        self.assertEqual((order.state, run.recipe_id, run.work_order_id, sum(run.input_ids.mapped('qty_kg'))), ('running', recipe, order, 20000.0))
        with self.assertRaises(UserError):
            order.action_finish()  # the run is not complete
        run.write({'output_ids': [Command.create({'product_id': self.flour.id, 'kind': 'flour', 'qty_kg': 14400.0}), Command.create({'product_id': self.bran.id, 'kind': 'bran', 'qty_kg': 4000.0})]})
        run.with_user(self.sup).action_complete()
        order.action_finish()
        self.assertEqual(order.state, 'done')
        self.assertEqual((order.actual_wheat_kg, order.actual_flour_kg), (20000.0, 14400.0))
        self.assertEqual(order.actual_extraction, 72.0)

    def test_work_order_refuses_when_the_silos_hold_too_little(self):
        recipe = self._recipe()
        order = self.env['mos.work.order'].create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 900000.0})
        with self.assertRaises(UserError):
            order.action_plan()
        with self.assertRaises(ValidationError):
            self.env['mos.work.order'].create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 0})
        cancel = self.env['mos.work.order'].create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0})
        cancel.action_plan()
        cancel.action_start()
        cancel.action_cancel()
        self.assertEqual(cancel.state, 'cancelled')
        self.assertFalse(self.env['mos.milling.run'].search([('work_order_id', '=', cancel.id)]))

    # -- silo dips -----------------------------------------------------------------------------------------------------
    def _strap(self, silo):
        for height, volume in ((0, 0), (200, 40), (400, 80), (800, 160)):
            self.env['mos.silo.strapping'].create({'silo_id': silo.id, 'height_cm': height, 'volume_m3': volume})

    def test_dip_height_becomes_kilograms_and_is_set_against_the_ledger(self):
        self._strap(self.silo_d)
        self.silo_d.bulk_density = 800.0
        self.assertAlmostEqual(self.silo_d.volume_at(300.0), 60.0)
        dip = self.env['mos.silo.dip'].with_user(self.store).create({'silo_id': self.silo_d.id, 'height_cm': 625.0})  # 125 m3 = 100 000 kg
        self.assertEqual((dip.measured_kg, dip.ledger_kg, dip.variance_kg), (100000.0, 100000.0, 0.0))
        self.assertTrue(dip.within_tolerance)
        self.assertRegex(dip.name, r'^SD-\d{4}-\d{5}$')
        off = self.env['mos.silo.dip'].with_user(self.store).create({'silo_id': self.silo_d.id, 'height_cm': 600.0})  # 120 m3 = 96 000 kg
        self.assertEqual(off.variance_kg, -4000.0)
        self.assertFalse(off.within_tolerance)
        self.assertTrue(self.env['mos.exception'].search([('kind', '=', 'dip_variance'), ('res_id', '=', off.id)]))
        adjustment = off.with_user(self.inv).action_adjust()
        self.assertEqual((adjustment.line_ids.system_qty, adjustment.line_ids.new_qty), (100000.0, 96000.0))
        self.assertEqual(off.action_adjust(), adjustment, 'once only')
        with self.assertRaises(UserError):
            off.write({'height_cm': 1})  # a reading is final

    def test_dip_needs_a_strapping_table_and_a_height_inside_it(self):
        with self.assertRaises(UserError):
            self.env['mos.silo.dip'].create({'silo_id': self.silo_b.id, 'height_cm': 100.0})
        self._strap(self.silo_b)
        with self.assertRaises(UserError):
            self.env['mos.silo.dip'].create({'silo_id': self.silo_b.id, 'height_cm': 900.0})
        with self.assertRaises(ValidationError):
            self.env['mos.silo.strapping'].create({'silo_id': self.silo_b.id, 'height_cm': -5, 'volume_m3': 1})

    # -- holds -----------------------------------------------------------------------------------------------------------
    def test_a_silo_on_hold_cannot_be_drawn_or_filled(self):
        hold = self.env['mos.silo.hold'].with_user(self.sup).create({'silo_id': self.silo_d.id, 'reason': 'fumigation', 'chemical': 'Phosphine tablets', 'planned_end': fields.Datetime.now() + timedelta(days=5)})
        self.assertTrue(self.silo_d.on_hold)
        recipe = self._recipe()
        run = self._run(recipe)
        with self.assertRaises(UserError):
            run.with_user(self.sup).action_complete()
        order = self.env['mos.work.order'].create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0})
        with self.assertRaises(UserError):
            order.action_plan()  # the only durum silo is on hold
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'HOLD-1', 'silo_id': self.silo_d.id})
        self.assertTrue(any('treatment hold' in p for p in visit._mos_preflight()))
        with self.assertRaises(ValidationError):
            self.env['mos.silo.hold'].create({'silo_id': self.silo_d.id, 'reason': 'treatment', 'planned_end': fields.Datetime.now() + timedelta(days=1)})  # already on hold
        with self.assertRaises(UserError):
            hold.with_user(self.sup).action_release('early')  # a supervisor cannot release
        with self.assertRaises(UserError):
            hold.with_user(self.inv).action_release()  # early, no reason
        hold.with_user(self.inv).action_release('Gas reading under 0.1 ppm after ventilation')
        self.assertEqual(hold.state, 'released')
        self.assertFalse(self.silo_d.on_hold)
        run.with_user(self.sup).action_complete()
        self.assertEqual(run.state, 'done')
