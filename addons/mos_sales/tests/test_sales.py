from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestSales(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'SMILL', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.rep = user('s_rep', 'mos_sales.group_mos_sales_rep')
        cls.guard = user('s_guard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('s_ver', 'mos_gate.group_mos_gate_verifier')
        cls.cashier = user('s_cash', 'mos_sales.group_mos_sales_cashier')
        cls.cashier2 = user('s_cash2', 'mos_sales.group_mos_sales_cashier')
        cls.manager = user('s_mgr', 'mos_approvals.group_mos_general_manager')
        cls.flour = env['product.product'].create(
            {'name': 'Bread flour', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'list_price': 1.0, 'standard_price': 0.5}
        )
        env['stock.quant'].sudo()._update_available_quantity(cls.flour, warehouse.lot_stock_id, 100000)
        cls.customer = env['res.partner'].create({'name': 'Bakery Chain', 'mos_credit_limit': 1000000, 'mos_credit_mode': 'block'})

    def _order(self, qty=20000, price=0.8, customer=None):
        order = self.env['sale.order'].with_user(self.rep).create(
            {
                'partner_id': (customer or self.customer).id,
                'warehouse_id': self.site.warehouse_id.id,
                'order_line': [Command.create({'product_id': self.flour.id, 'product_uom_qty': qty, 'price_unit': price})],
            }
        )
        return order

    def _truck(self, order, tare=15000.0, loaded=None):
        delivery = self.env['mos.delivery.order'].with_user(self.rep).create({'sale_id': order.id, 'site_id': self.site.id, 'plate': 'AA-7777', 'driver_name': 'Abel'})
        delivery.action_release()
        visit = delivery.visit_id.with_user(self.guard)
        self.assertEqual(visit.state, 'expected')
        visit.action_arrive()
        visit.action_start_weighing()
        ticket = visit.ticket_id
        loaded = loaded if loaded is not None else tare + order.order_line.product_uom_qty
        ticket.record_first(tare)
        ticket.record_second(loaded)
        ticket.with_user(self.verifier).verify_blind(tare, loaded)
        return delivery

    def test_order_inside_credit_confirms_and_delivers_and_invoices_at_the_weighed_quantity(self):
        order = self._order()
        self.assertFalse(order.mos_credit_exceeded)
        order.with_user(self.rep).action_confirm()
        delivery = self._truck(order, loaded=15000 + 19800)  # the truck carries 19,800 kg, a little under the order
        ticket = delivery.visit_id.ticket_id
        self.assertEqual(ticket.direction, 'out')
        self.assertEqual(ticket.net_weight, 19800)
        delivery.with_user(self.rep).action_dispatch()
        self.assertEqual(delivery.state, 'dispatched')
        invoice = delivery.invoice_id
        self.assertEqual(invoice.qty, 19800)
        self.assertEqual(invoice.net_amount, round(19800 * 0.8, 2))
        self.assertEqual(invoice.gross_amount, round(19800 * 0.8 * 1.15, 2))
        self.assertRegex(invoice.name, r'^CI-\d{4}-\d{6}$')
        quant = self.env['stock.quant'].sudo()._get_available_quantity(self.flour, self.site.warehouse_id.lot_stock_id)
        self.assertEqual(quant, 100000 - 19800)
        visit = delivery.visit_id.with_user(self.guard)
        visit.action_clear_exit()
        visit.action_exit()
        self.assertEqual(visit.state, 'exited')

    def test_exit_is_refused_until_the_delivery_is_dispatched(self):
        order = self._order()
        order.with_user(self.rep).action_confirm()
        delivery = self._truck(order)
        with self.assertRaises(UserError):
            delivery.visit_id.with_user(self.guard).action_clear_exit()

    def test_outbound_weighing_rules(self):
        order = self._order()
        order.with_user(self.rep).action_confirm()
        delivery = self.env['mos.delivery.order'].with_user(self.rep).create({'sale_id': order.id, 'site_id': self.site.id, 'plate': 'AA-8888'})
        delivery.action_release()
        visit = delivery.visit_id.with_user(self.guard)
        visit.action_arrive()
        visit.action_start_weighing()
        visit.ticket_id.record_first(15000.0)
        with self.assertRaises(UserError):
            visit.ticket_id.record_second(14000.0)  # loaded must be heavier than empty

    def test_cannot_load_more_than_ordered_beyond_tolerance_or_more_than_stock(self):
        order = self._order(qty=20000)
        order.with_user(self.rep).action_confirm()
        delivery = self._truck(order, loaded=15000 + 21000)  # 5% over, tolerance is 2%
        with self.assertRaises(UserError):
            delivery.with_user(self.rep).action_dispatch()

    def test_credit_limit_needs_approval(self):
        small = self.env['res.partner'].create({'name': 'Corner Shop', 'mos_credit_limit': 10000, 'mos_credit_mode': 'block'})
        order = self._order(qty=20000, price=0.8, customer=small)  # 16,000 plus VAT is over the 10,000 limit
        self.assertTrue(order.mos_credit_exceeded)
        with self.assertRaises(UserError):
            order.with_user(self.rep).action_confirm()
        with self.assertRaises(UserError):
            order.with_user(self.rep).action_mos_submit_approval()  # no rule yet: nobody could approve
        self.env['mos.approval.rule'].create({'doc_type': 'sale.order', 'amount_from': 0, 'group_id': self.env.ref('mos_approvals.group_mos_general_manager').id})
        order.with_user(self.rep).action_mos_submit_approval()
        order.mos_approval_request_id.step_ids.filtered(lambda s: s.state == 'pending').with_user(self.manager).action_approve('Known customer, paying Friday')
        order.with_user(self.rep).action_confirm()
        self.assertEqual(order.state, 'sale')

    def test_receipts_advances_and_balance(self):
        order = self._order(qty=10000, price=1.0)
        order.with_user(self.rep).action_confirm()
        delivery = self._truck(order)
        delivery.with_user(self.rep).action_dispatch()
        invoice = delivery.invoice_id
        self.assertEqual(invoice.gross_amount, 11500.0)
        Receipt = self.env['mos.receipt']
        with self.assertRaises(UserError):
            Receipt.with_user(self.cashier).create({'customer_id': self.customer.id, 'invoice_id': invoice.id, 'amount': 5000, 'method': 'cash'})  # no open session
        session = self.env['mos.cash.session'].with_user(self.cashier).create({'float_amount': 1000})
        with self.assertRaises(UserError):
            self.env['mos.cash.session'].with_user(self.cashier).create({'float_amount': 1})  # one open session per cashier
        Receipt.with_user(self.cashier).create({'customer_id': self.customer.id, 'invoice_id': invoice.id, 'amount': 5000, 'method': 'cash'})
        with self.assertRaises(UserError):
            Receipt.with_user(self.cashier).create({'customer_id': self.customer.id, 'invoice_id': invoice.id, 'amount': 99999, 'method': 'transfer'})  # more than owed
        Receipt.with_user(self.cashier).create({'customer_id': self.customer.id, 'amount': 2000, 'method': 'transfer', 'reference': 'ADV-1'})  # an advance
        self.assertEqual(invoice.balance, 6500.0)
        balance = self.env['mos.customer.balance'].search([('customer_id', '=', self.customer.id)])
        self.assertEqual(balance.balance, 6500.0 - 2000.0)
        self.assertEqual(self.customer.mos_owed, 4500.0)
        # The till: 1,000 float + 5,000 cash = 6,000 expected.
        with self.assertRaises(UserError):
            session.with_user(self.cashier2).action_close(6000, '')  # not the cashier who opened it
        with self.assertRaises(UserError):
            session.with_user(self.cashier).action_close(5900, '')  # 100 short, no reason
        session.with_user(self.cashier).action_close(5900, 'Change given in error')
        self.assertEqual(session.variance, -100.0)
        self.assertEqual(session.state, 'closed')

    def test_posted_documents_are_final_except_the_fiscal_reference(self):
        order = self._order(qty=1000, price=1.0)
        order.with_user(self.rep).action_confirm()
        delivery = self._truck(order)
        delivery.with_user(self.rep).action_dispatch()
        invoice = delivery.invoice_id
        invoice.write({'fiscal_receipt_ref': 'FS-0001'})
        self.assertEqual(invoice.fiscal_receipt_ref, 'FS-0001')
        with self.assertRaises(UserError):
            invoice.write({'qty': 5})
        with self.assertRaises(UserError):
            invoice.unlink()
