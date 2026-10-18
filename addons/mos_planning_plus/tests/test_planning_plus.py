from datetime import timedelta
from math import ceil

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup


@tagged('post_install', '-at_install')
class TestPlanningPlus(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        env = cls.env
        cls.stock = env['stock.warehouse'].search([], limit=1).lot_stock_id

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.sup = user('plsup', 'mos_manufacturing.group_mos_production_supervisor')
        cls.sup2 = user('plsup2', 'mos_manufacturing.group_mos_production_supervisor')
        cls.rep = user('plrep', 'mos_sales.group_mos_sales_rep')
        kg = env.ref('uom.product_uom_kgm')
        cls.durum = env['product.product'].create({'name': 'Durum PL', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 22.0})
        cls.bread = env['product.product'].create({'name': 'Bread wheat PL', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 19.0})
        cls.flour = env['product.product'].create({'name': 'Semolina PL', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 30.0, 'list_price': 35.0})
        cls.customer = env['res.partner'].create({'name': 'Planning bakery', 'customer_rank': 1, 'mos_credit_limit': 100000000, 'mos_credit_mode': 'block'})
        cls.centre = env['mos.work.centre'].create({'name': 'Mill line 1', 'code': 'PL1', 'site_id': cls.site.id, 'kind': 'milling', 'capacity_kg_h': 5000, 'labour_rate_h': 100.0, 'overhead_rate_h': 400.0})
        cls.packing = env['mos.work.centre'].create({'name': 'Packing 1', 'code': 'PK1', 'site_id': cls.site.id, 'kind': 'packing', 'capacity_kg_h': 10000})

    @classmethod
    def _set_stock(cls, product, qty, location=None):
        quant = cls.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id, 'location_id': (location or cls.stock).id, 'inventory_quantity': qty})
        quant.action_apply_inventory()

    def _recipe(self):
        recipe = self.env['mos.recipe'].with_user(self.sup).create(
            {
                'product_id': self.flour.id,
                'target_extraction_pct': 72.0,
                'line_ids': [Command.create({'wheat_id': self.durum.id, 'share_pct': 70.0}), Command.create({'wheat_id': self.bread.id, 'share_pct': 30.0})],
            }
        )
        recipe.with_user(self.sup2).action_approve()
        return recipe

    def _routing(self, approve=True):
        routing = self.env['mos.routing'].with_user(self.sup).create(
            {
                'product_id': self.flour.id,
                'op_ids': [
                    Command.create({'sequence': 10, 'name': 'Milling', 'work_centre_id': self.centre.id, 'setup_minutes': 30, 'run_rate_kg_h': 5000, 'reporting_point': 'Mill floor'}),
                    Command.create({'sequence': 20, 'name': 'Packing', 'work_centre_id': self.packing.id, 'setup_minutes': 0, 'run_rate_kg_h': 10000}),
                ],
            }
        )
        if approve:
            routing.with_user(self.sup2).action_approve()
        return routing

    # -- routings -----------------------------------------------------------------------------------------------------
    def test_routing_versions_and_approval(self):
        routing = self._routing(approve=False)
        self.assertEqual(routing.version, 1)
        with self.assertRaises(UserError):
            routing.with_user(self.sup).action_approve()  # its author
        with self.assertRaises(UserError):
            routing.with_user(self.buyer).action_approve()  # not a supervisor
        routing.with_user(self.sup2).action_approve()
        self.assertEqual(self.env['mos.routing'].in_force(self.flour), routing)
        with self.assertRaises(UserError):
            routing.op_ids[0].write({'setup_minutes': 5})  # an approved routing is frozen
        with self.assertRaises(UserError):
            self.env['mos.routing.op'].create({'routing_id': routing.id, 'name': 'Extra', 'work_centre_id': self.centre.id, 'run_rate_kg_h': 100})
        second = routing.with_user(self.sup).action_new_version()
        self.assertEqual((second.version, second.state, len(second.op_ids)), (2, 'draft', 2))
        second.with_user(self.sup2).action_approve()
        self.assertEqual((routing.state, self.env['mos.routing'].in_force(self.flour)), ('retired', second))

    def test_routing_empty_or_bad_rates_are_refused(self):
        empty = self.env['mos.routing'].with_user(self.sup).create({'product_id': self.flour.id})
        with self.assertRaises(UserError):
            empty.with_user(self.sup2).action_approve()
        with self.assertRaises(ValidationError):
            self.env['mos.routing.op'].create({'routing_id': empty.id, 'name': 'Bad', 'work_centre_id': self.centre.id, 'run_rate_kg_h': 0})

    def test_work_order_takes_the_routing_and_shows_the_operations(self):
        routing = self._routing()
        recipe = self._recipe()
        self.assertEqual(routing.minutes_for(10000.0)[0], 210.0, '30 + 120 min milling, 60 min packing')
        order = self.env['mos.work.order'].with_user(self.sup).create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0, 'work_centre_id': self.centre.id})
        self.assertEqual(order.routing_id, routing)
        self.assertEqual(order.planned_minutes, 210.0)
        self.assertEqual(len(order.operation_sheet.splitlines()), 2)
        self.assertIn('Mill floor', order.operation_sheet)
        self.assertIn('Packing', order.operation_sheet)
        plain = self.env['mos.work.order'].with_user(self.sup).create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0, 'work_centre_id': self.centre.id, 'routing_id': False})
        self.assertEqual(plain.routing_id, routing, 'the approved routing is the default')
        self.env['mos.routing'].search([]).sudo().write({'state': 'retired'})
        no_routing = self.env['mos.work.order'].with_user(self.sup).create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0, 'work_centre_id': self.centre.id})
        self.assertEqual(no_routing.planned_minutes, 120.0, 'without a routing the centre capacity sets the time')

    # -- costing ------------------------------------------------------------------------------------------------------
    def test_worked_example_of_conversion_cost_and_variances(self):
        self._routing_milling_only = self.env['mos.routing'].with_user(self.sup).create({'product_id': self.flour.id, 'op_ids': [Command.create({'name': 'Milling', 'work_centre_id': self.centre.id, 'setup_minutes': 30, 'run_rate_kg_h': 5000})]})
        self._routing_milling_only.with_user(self.sup2).action_approve()
        recipe = self._recipe()
        order = self.env['mos.work.order'].with_user(self.sup).create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0, 'work_centre_id': self.centre.id, 'crew_size': 4})
        self.assertEqual(order.planned_minutes, 150.0)
        for worker in ('Abebe', 'Chaltu', 'Dawit', 'Eden'):
            self.env['mos.labour.log'].with_user(self.sup).create({'order_id': order.id, 'worker': worker, 'hours': 3.0})
        order.overhead_incurred = 1100.0
        figures = order._mos_variances(10200.0, 7344.0)
        self.assertEqual(figures['labour_hours'], 12.0)
        self.assertEqual(figures['labour_cost'], 1200.0, '12 hours at 100')
        self.assertEqual(figures['standard_labour'], 1000.0, '2.5 hours x 100 x 4 people')
        self.assertEqual(figures['labour_variance'], 200.0)
        self.assertEqual(figures['overhead_absorbed'], 1000.0, '2.5 hours x 400')
        self.assertEqual(figures['absorption_variance'], 100.0, 'under-absorbed')
        self.assertAlmostEqual(figures['usage_variance'], 200 * 21.1, places=2)
        self.assertEqual(figures['yield_variance'], 0.0, '72 percent as targeted')
        self.assertAlmostEqual(figures['conversion_cost_per_kg'], 2300.0 / 7344.0, places=4)
        worse = order._mos_variances(10000.0, 7000.0)
        self.assertAlmostEqual(worse['yield_variance'], (72.0 - 70.0) / 100 * 10000 * 30.0, places=2, msg='2 points of flour lost at the flour price')
        self.assertEqual(worse['usage_variance'], 0.0)
        self.assertEqual(self.env['mos.labour.log'].search([('order_id', '=', order.id)])[:1].rate, 100.0, 'the rate comes from the work centre')
        with self.assertRaises(ValidationError):
            self.env['mos.labour.log'].create({'order_id': order.id, 'worker': 'Nobody', 'hours': 30})

    # -- MRP-lite -----------------------------------------------------------------------------------------------------
    def _open_demand(self, qty):
        order = self.env['sale.order'].with_user(self.rep).create({'partner_id': self.customer.id, 'order_line': [Command.create({'product_id': self.flour.id, 'product_uom_qty': qty, 'price_unit': 35.0})]})
        order.with_user(self.rep).action_confirm()
        return order

    def test_suggestions_make_then_accept_creates_a_work_order(self):
        recipe = self._recipe()
        self._set_stock(self.flour, 15000.0)
        self._set_stock(self.durum, 100000.0)
        self._set_stock(self.bread, 100000.0)
        sale = self._open_demand(40000.0)
        self.env['mos.work.order'].with_user(self.sup).create({'site_id': self.site.id, 'recipe_id': recipe.id, 'planned_kg': 10000.0})
        suggestions = self.env['mos.mrp.suggestion'].with_user(self.sup).generate()
        make = suggestions.filtered(lambda s: s.kind == 'work_order')
        self.assertEqual(len(make), 1)
        # open demand 40,000 (15,000 of it is covered by stock after the reservation, but demand is netted against what is available)
        self.assertIn(sale.name, make.peg)
        self.assertEqual(make.product_id, self.flour)
        planned_flour = 10000.0 * 0.72
        self.assertEqual(make.qty, ceil((self.env['mos.planner.report'].search([('product_id', '=', self.flour.id)]).shortfall - planned_flour) / 0.72))
        self.assertFalse(suggestions.filtered(lambda s: s.kind == 'purchase'), 'plenty of wheat in stock')
        with self.assertRaises(UserError):
            make.with_user(self.buyer).action_accept()  # not a production supervisor
        make.with_user(self.sup).action_accept()
        self.assertEqual(make.state, 'accepted')
        order = self.env['mos.work.order'].search([('planned_kg', '=', make.qty)])
        self.assertEqual((len(order), order.state), (1, 'draft'))
        self.assertEqual(make.result, order.display_name)
        with self.assertRaises(UserError):
            make.with_user(self.sup).action_accept()
        again = self.env['mos.mrp.suggestion'].with_user(self.sup).generate()
        self.assertFalse(again.filtered(lambda s: s.kind == 'work_order'), 'the new work order now covers the shortfall')

    def test_suggestion_to_buy_wheat_when_stock_and_orders_fall_short(self):
        self._recipe()
        self._set_stock(self.flour, 0.0)
        self._set_stock(self.durum, 10000.0)
        self._set_stock(self.bread, 100000.0)
        self._open_demand(36000.0)  # 50,000 kg of wheat: 35,000 durum, 15,000 bread
        suggestions = self.env['mos.mrp.suggestion'].with_user(self.sup).generate()
        buy = suggestions.filtered(lambda s: s.kind == 'purchase')
        self.assertEqual(buy.product_id, self.durum)
        self.assertEqual(buy.qty, ceil(50000 * 0.7 - 10000.0))
        self.assertTrue(buy.peg, 'pegged to the work order suggestion behind it')
        self.assertEqual(buy.parent_id.kind, 'work_order')
        # 5,000 kg already on order from a supplier counts
        order = self.env['purchase.order'].with_user(self.buyer).create({'partner_id': self.s1.id, 'order_line': [Command.create({'product_id': self.durum.id, 'product_qty': 5000.0, 'price_unit': 22.0})]})
        order.with_user(self.buyer).button_confirm()
        self.env.flush_all()
        later = self.env['mos.mrp.suggestion'].with_user(self.sup).generate().filtered(lambda s: s.kind == 'purchase')
        self.assertEqual(later.qty, ceil(50000 * 0.7 - 10000.0) - 5000, 'what is already on order is netted off')
        with self.assertRaises(UserError):
            later.with_user(self.sup).action_accept()  # not procurement
        later.with_user(self.buyer).action_accept()
        request = self.env['mos.purchase.request'].search([('reason', 'ilike', 'available')], limit=1)
        self.assertTrue(request)
        self.assertEqual(request.line_ids.product_id, self.durum)
        dismissed = self.env['mos.mrp.suggestion'].with_user(self.sup).generate().filtered(lambda s: s.kind == 'work_order')
        dismissed.action_dismiss()
        self.assertEqual(dismissed.state, 'dismissed')

    # -- reservations -------------------------------------------------------------------------------------------------
    def test_a_confirmed_order_reserves_only_what_is_available(self):
        self._set_stock(self.flour, 300.0)
        first = self._open_demand(1000.0)
        res = first.mos_reservation_ids
        self.assertEqual((res.qty, res.state), (300.0, 'active'))
        self.assertTrue(any('Not enough available-to-use stock' in (m.body or '') for m in first.message_ids), 'the shortfall is said plainly')
        self.assertEqual(self.flour.mos_free_qty, 0.0)
        second = self._open_demand(200.0)
        self.assertFalse(second.mos_reservation_ids, 'the same flour is not promised twice')
        first._action_cancel()
        self.assertEqual(res.state, 'released')
        self.assertEqual(self.flour.mos_free_qty, 300.0)
        third = self._open_demand(100.0)
        self.assertEqual(third.mos_reservation_ids.qty, 100.0)
        self.assertEqual(self.flour.mos_free_qty, 200.0)
        self.assertEqual(self.env['mos.reservation']._cron_release(), 0)

    def test_reservation_shrinks_with_delivery_and_is_released_when_complete(self):
        self._set_stock(self.flour, 1000.0)
        order = self._open_demand(400.0)
        res = order.mos_reservation_ids
        self.assertEqual(res.remaining, 400.0)
        order.order_line.qty_delivered = 150.0
        res.invalidate_recordset()
        self.assertEqual(res.remaining, 250.0)
        order.order_line.qty_delivered = 400.0
        self.assertEqual(self.env['mos.reservation']._cron_release(), 1)
        self.assertEqual(res.state, 'released')

    # -- purchase contracts ---------------------------------------------------------------------------------------
    def _contract(self, qty=100000.0, price=20.0, days=30):
        contract = self.env['mos.purchase.contract'].with_user(self.buyer).create(
            {'supplier_id': self.s1.id, 'product_id': self.wheat.id, 'qty': qty, 'price': price, 'valid_from': fields.Date.today() - timedelta(days=1), 'valid_to': fields.Date.today() + timedelta(days=days)}
        )
        return contract

    def test_contract_call_offs_cannot_exceed_the_contract(self):
        contract = self._contract()
        with self.assertRaises(UserError):
            contract.with_user(self.buyer).action_activate()  # not a manager
        with self.assertRaises(UserError):
            self.env['mos.purchase.contract'].with_user(self.manager).create({'supplier_id': self.s1.id, 'product_id': self.wheat.id, 'qty': 1000, 'price': 20, 'valid_to': fields.Date.today()}).with_user(self.manager).action_activate()  # its author
        contract.with_user(self.manager).action_activate()
        self.assertEqual(contract.state, 'active')
        first = contract.with_user(self.buyer).action_new_calloff(60000.0)
        first.with_user(self.buyer).button_confirm()
        self.assertEqual((contract.called_off, contract.remaining), (60000.0, 40000.0))
        with self.assertRaises(ValidationError):
            contract.with_user(self.buyer).action_new_calloff(50000.0)  # only 40,000 left
        second = contract.with_user(self.buyer).action_new_calloff(40000.0)
        second.with_user(self.buyer).button_confirm()
        self.assertEqual(contract.remaining, 0.0)
        with self.assertRaises(ValidationError):
            contract.with_user(self.buyer).action_new_calloff(1.0)
        with self.assertRaises(UserError):
            contract.action_cancel()  # it has call-offs
        contract.action_close('Fully called off')
        self.assertEqual(contract.state, 'closed')

    def test_contract_price_dates_and_supplier_are_enforced_at_confirmation(self):
        contract = self._contract()
        contract.with_user(self.manager).action_activate()
        order = contract.with_user(self.buyer).action_new_calloff(1000.0)
        order.order_line.price_unit = 21.0
        with self.assertRaises(UserError):
            order.with_user(self.buyer).button_confirm()  # not the agreed price
        order.order_line.price_unit = 20.0
        other = self.env['purchase.order'].with_user(self.buyer).create({'partner_id': self.s2.id, 'mos_contract_id': contract.id, 'order_line': [Command.create({'product_id': self.wheat.id, 'product_qty': 10.0, 'price_unit': 20.0})]})
        with self.assertRaises(UserError):
            other.with_user(self.buyer).button_confirm()  # a different supplier
        old = self._contract(days=-1)
        old.with_user(self.manager).action_activate()
        late = old.with_user(self.buyer).action_new_calloff(10.0)
        with self.assertRaises(UserError):
            late.with_user(self.buyer).button_confirm()  # the contract has expired
        with self.assertRaises(ValidationError):
            self.env['mos.purchase.contract'].create({'supplier_id': self.s1.id, 'product_id': self.wheat.id, 'qty': 0, 'price': 1, 'valid_to': fields.Date.today()})
        self.assertTrue(contract.name.startswith('CTR-'))
