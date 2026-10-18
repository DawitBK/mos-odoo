from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase, new_test_user


class CommercialSetup:
    @classmethod
    def _setup_commercial(cls):
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLC2', 'warehouse_id': warehouse.id})
        cls.stock = warehouse.lot_stock_id

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.rep = user('crep', 'mos_sales.group_mos_sales_rep')
        cls.rep2 = user('crep2', 'mos_sales.group_mos_sales_rep')
        cls.smgr = user('csmgr', 'mos_commercial.group_mos_sales_manager')
        cls.fin = user('cfin', 'mos_approvals.group_mos_finance_manager')
        cls.store = user('cstore', 'mos_inbound.group_mos_storekeeper')
        cls.labmgr = user('clabmgr', 'mos_lab.group_mos_lab_manager')
        cls.kg = env.ref('uom.product_uom_kgm')
        cls.flour = env['product.product'].create({'name': 'Commercial flour', 'type': 'consu', 'is_storable': True, 'uom_id': cls.kg.id, 'standard_price': 25.0, 'list_price': 35.0})
        cls.customer = env['res.partner'].with_user(cls.rep).create({'name': 'Mekelle Bakery', 'customer_rank': 1})

    def _invoice(self, qty=1000.0, price=35.0):
        return self.env['mos.customer.invoice'].create({'customer_id': self.customer.id, 'product_id': self.flour.id, 'qty': qty, 'unit_price': price})


@tagged('post_install', '-at_install')
class TestCommercial(CommercialSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial()

    def _order(self, price=35.0, discount=0.0, customer=None):
        return self.env['sale.order'].with_user(self.rep).create(
            {'partner_id': (customer or self.customer).id, 'order_line': [Command.create({'product_id': self.flour.id, 'product_uom_qty': 1000.0, 'price_unit': price, 'discount': discount})]}
        )

    def _params(self, **kw):
        icp = self.env['ir.config_parameter'].sudo()
        for key, value in kw.items():
            icp.set_str(f'mos_commercial.{key}', 'True' if value is True else 'False' if value is False else str(value))

    # -- customers and credit ---------------------------------------------------------------------------------
    def test_customer_must_be_approved_by_someone_else_before_an_order(self):
        self._params(enforce_customer_approval=True)
        order = self._order()
        with self.assertRaises(UserError):
            order.with_user(self.rep).action_confirm()
        with self.assertRaises(UserError):
            self.customer.with_user(self.rep).action_approve_customer()  # not a manager
        self.customer.with_user(self.smgr).action_approve_customer()
        self.assertEqual(self.customer.mos_customer_state, 'approved')
        manager_made = self.env['res.partner'].with_user(self.smgr).create({'name': 'Self made', 'customer_rank': 1})
        with self.assertRaises(UserError):
            manager_made.with_user(self.smgr).action_approve_customer()
        self.customer.with_user(self.fin).action_block_customer('Bounced cheques')
        with self.assertRaises(UserError):
            self._order().with_user(self.rep).action_confirm()

    def test_credit_limit_changes_only_through_an_approved_request(self):
        self._params(control_credit_changes=True)
        with self.assertRaises(UserError):
            self.customer.write({'mos_credit_limit': 500000})
        change = self.env['mos.credit.change'].with_user(self.smgr).create({'partner_id': self.customer.id, 'new_limit': 500000.0, 'reason': 'Five years of on-time payment'})
        self.assertEqual(change.old_limit, 0.0)
        with self.assertRaises(UserError):
            change.with_user(self.smgr).action_approve()  # the requester
        with self.assertRaises(UserError):
            change.with_user(self.rep).action_approve()  # not finance
        change.with_user(self.fin).action_approve()
        self.assertEqual(self.customer.mos_credit_limit, 500000.0)
        self.assertEqual(change.state, 'applied')
        with self.assertRaises(UserError):
            change.write({'new_limit': 1})
        with self.assertRaises(ValidationError):
            self.env['mos.credit.change'].create({'partner_id': self.customer.id, 'new_limit': -5, 'reason': 'x'})

    # -- prices ------------------------------------------------------------------------------------------------
    def test_price_below_the_floor_or_a_big_discount_needs_a_sales_manager(self):
        self._params(enforce_pricing=True, enforce_customer_approval=False)
        self.env['mos.price.floor'].create({'product_id': self.flour.id, 'floor_price': 33.0})
        ok = self._order(price=35.0)
        self.assertFalse(ok.mos_price_exception)
        cheap = self._order(price=31.0)
        self.assertTrue(cheap.mos_price_exception)
        discounted = self._order(price=36.0, discount=5.0)  # 34.2 net, above the floor, but 5 % is over the 3 % limit
        self.assertTrue(discounted.mos_price_exception)
        pricelist = cheap.pricelist_id
        pricelist.sudo().write({'mos_state': 'approved'})
        with self.assertRaises(UserError):
            cheap.with_user(self.rep).action_confirm()
        with self.assertRaises(UserError):
            cheap.with_user(self.rep).action_approve_prices('please')  # not a manager
        with self.assertRaises(UserError):
            cheap.with_user(self.smgr).action_approve_prices('')
        cheap.with_user(self.smgr).action_approve_prices('Long-standing customer, market price fell')
        self.assertEqual(cheap.mos_price_approved_by_id, self.smgr)

    def test_price_list_must_be_approved_by_another_sales_manager(self):
        self._params(enforce_pricing=True, enforce_customer_approval=False)
        pricelist = self.env['product.pricelist'].with_user(self.smgr).create({'name': 'Wholesale 2026'})
        self.assertEqual(pricelist.mos_state, 'draft')
        order = self._order()
        order.sudo().pricelist_id = pricelist
        with self.assertRaises(UserError):
            order.with_user(self.rep).action_confirm()
        with self.assertRaises(UserError):
            pricelist.with_user(self.smgr).action_mos_approve()  # its author
        other = new_test_user(self.env, login='csmgr2', groups='mos_commercial.group_mos_sales_manager')
        pricelist.with_user(other).action_mos_approve()
        self.assertEqual(pricelist.mos_state, 'approved')
        pricelist.sudo().write({'item_ids': [Command.create({'applied_on': '3_global', 'compute_price': 'discount', 'price_discount': 5})]})
        self.assertEqual(pricelist.mos_state, 'draft', 'changing an approved list sends it back for approval')

    # -- credit notes ---------------------------------------------------------------------------------------------
    def test_credit_note_lowers_the_invoice_and_needs_approval_above_the_band(self):
        reason = self.env['mos.reason'].create({'domain': 'credit', 'code': 'SHORT', 'name': 'Shortage on delivery'})
        invoice = self._invoice(qty=1000.0, price=35.0)  # 35,000 + 15 % VAT
        self.assertEqual(invoice.balance, 40250.0)
        self.env['mos.approval.rule'].create({'doc_type': 'mos.credit.note', 'amount_from': 1000, 'group_id': self.env.ref('mos_approvals.group_mos_finance_manager').id})
        note = self.env['mos.credit.note'].with_user(self.rep).create({'customer_id': self.customer.id, 'invoice_id': invoice.id, 'reason_id': reason.id, 'amount': 2300.0})
        with self.assertRaises(UserError):
            note.with_user(self.rep).action_post()  # not approved
        note.with_user(self.rep).action_submit()
        self.assertEqual(note.state, 'pending')
        note.mos_approval_request_id.current_step_id.with_user(self.fin).action_approve('Weighbridge ticket confirms 66 kg short')
        self.assertEqual(note.state, 'approved')
        note.with_user(self.fin).action_post()
        self.assertEqual(note.state, 'posted')
        self.assertRegex(note.name, r'^CN-\d{4}-\d{5}$')
        invoice.invalidate_recordset()
        self.assertEqual(invoice.balance, 40250.0 - 2300.0)
        with self.assertRaises(UserError):
            note.write({'amount': 1})  # posted credit notes are final
        with self.assertRaises(ValidationError):
            self.env['mos.credit.note'].create({'customer_id': self.customer.id, 'reason_id': reason.id, 'amount': 0})
        too_much = self.env['mos.credit.note'].create({'customer_id': self.customer.id, 'invoice_id': invoice.id, 'reason_id': reason.id, 'amount': 99999999.0})
        with self.assertRaises(UserError):
            too_much.action_submit()

    def test_a_small_credit_note_needs_no_approval_but_not_the_authors_posting(self):
        reason = self.env['mos.reason'].create({'domain': 'credit', 'code': 'PRICE', 'name': 'Price correction'})
        note = self.env['mos.credit.note'].with_user(self.rep).create({'customer_id': self.customer.id, 'invoice_id': self._invoice().id, 'reason_id': reason.id, 'amount': 100.0})
        note.with_user(self.rep).action_submit()
        self.assertEqual(note.state, 'approved')
        with self.assertRaises(UserError):
            note.with_user(self.rep).action_post()
        note.with_user(self.smgr).action_post()
        self.assertEqual(note.state, 'posted')

    # -- returns ----------------------------------------------------------------------------------------------------
    def test_returned_goods_go_to_quarantine_then_restock_or_scrap(self):
        reason = self.env['mos.reason'].create({'domain': 'customer_return', 'code': 'DMG', 'name': 'Damaged bags'})
        ret = self.env['mos.customer.return'].with_user(self.rep).create({'site_id': self.site.id, 'customer_id': self.customer.id, 'product_id': self.flour.id, 'qty': 500.0, 'reason_id': reason.id})
        with self.assertRaises(UserError):
            ret.with_user(self.store).action_decide('restock')  # not received yet
        ret.with_user(self.store).action_receive()
        quarantine = self.env['mos.stock.status.change'].status_location(self.site, 'quarantine')
        self.assertEqual(self._qty(quarantine), 500.0)
        self.assertEqual(self._qty(self.stock), 0.0)
        with self.assertRaises(UserError):
            ret.with_user(self.store).action_decide('restock')  # a storekeeper cannot decide
        ret.with_user(self.labmgr).action_decide('restock', 'Packaging intact, flour dry')
        self.assertEqual((ret.state, ret.decision), ('decided', 'restock'))
        self.assertEqual(self._qty(self.stock), 500.0)
        self.assertEqual(self._qty(quarantine), 0.0)
        ret.with_user(self.rep).action_credit()
        self.assertEqual(ret.credit_note_id.amount, round(500.0 * 35.0 * 1.15, 2))
        self.assertEqual(ret.credit_note_id.return_id, ret)
        scrap = self.env['mos.customer.return'].create({'site_id': self.site.id, 'customer_id': self.customer.id, 'product_id': self.flour.id, 'qty': 200.0, 'reason_id': reason.id})
        scrap.with_user(self.store).action_receive()
        scrap.with_user(self.labmgr).action_decide('scrap')
        self.assertEqual(self._qty(quarantine), 0.0)
        self.assertEqual(self._qty(self.stock), 500.0, 'scrapped goods never reach available stock')
        with self.assertRaises(ValidationError):
            self.env['mos.customer.return'].create({'site_id': self.site.id, 'customer_id': self.customer.id, 'product_id': self.flour.id, 'qty': 0, 'reason_id': reason.id})

    def _qty(self, location):
        return sum(self.env['stock.quant'].search([('product_id', '=', self.flour.id), ('location_id', '=', location.id)]).mapped('quantity'))

    # -- statement and fiscal exception ---------------------------------------------------------------------------
    def test_statement_runs_the_balance(self):
        reason = self.env['mos.reason'].create({'domain': 'credit', 'code': 'ST', 'name': 'Statement test'})
        old = self._invoice(qty=100.0, price=35.0)
        old.sudo().write({'date': fields.Date.today() - timedelta(days=45)}) if False else None
        invoice = self._invoice(qty=1000.0, price=35.0)
        note = self.env['mos.credit.note'].create({'customer_id': self.customer.id, 'invoice_id': invoice.id, 'reason_id': reason.id, 'amount': 1000.0})
        note.action_submit()
        note.with_user(self.smgr).action_post()
        statement = self.env['mos.customer.statement'].create({'customer_id': self.customer.id, 'date_from': fields.Date.today() - timedelta(days=10), 'date_to': fields.Date.today()})
        statement.action_build()
        self.assertEqual(statement.opening, 0.0)
        self.assertEqual(len(statement.line_ids), 3)
        self.assertEqual(statement.closing, round(old.gross_amount + invoice.gross_amount - 1000.0, 2))
        self.assertEqual(statement.line_ids[-1].balance, statement.closing)
        with self.assertRaises(UserError):
            self.env['mos.customer.statement'].create({'customer_id': self.customer.id, 'date_from': fields.Date.today(), 'date_to': fields.Date.today() - timedelta(days=1)}).action_build()

    def test_missing_fiscal_receipt_numbers_become_exceptions(self):
        invoice = self._invoice()
        invoice.sudo().write({'fiscal_receipt_ref': False})
        self.env.cr.execute('UPDATE mos_customer_invoice SET date = %s WHERE id = %s', [fields.Date.today() - timedelta(days=5), invoice.id])
        invoice.invalidate_recordset()
        self.env['mos.exception']._cron_scan()
        found = self.env['mos.exception'].search([('kind', '=', 'fiscal_missing'), ('res_id', '=', invoice.id)])
        self.assertEqual(len(found), 1)
        invoice.sudo().write({'fiscal_receipt_ref': 'FS-0001234'})
        self.env['mos.exception']._cron_scan()
        self.assertEqual(found.state, 'resolved')


@tagged('post_install', '-at_install')
class TestProofOfDelivery(CommercialSetup, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial()
        order = cls.env['sale.order'].create({'partner_id': cls.customer.id, 'order_line': [Command.create({'product_id': cls.flour.id, 'product_uom_qty': 1000.0, 'price_unit': 35.0})]})
        cls.delivery = cls.env['mos.delivery.order'].create({'sale_id': order.id, 'site_id': cls.site.id})

    def _dispatched(self):
        self.delivery.sudo().write({'state': 'dispatched'})

    def test_link_is_single_use_and_expires(self):
        with self.assertRaises(UserError):
            self.delivery.action_pod_link()  # not dispatched
        self._dispatched()
        path = self.delivery.action_pod_link()
        token = self.delivery.pod_token
        self.assertEqual(path, f'/mos/pod/{token}')
        self.assertEqual(self.env['mos.delivery.order'].pod_for_token(token), self.delivery)
        self.assertFalse(self.env['mos.delivery.order'].pod_for_token('nonsense'))
        self.delivery.sudo().pod_token_expires = fields.Datetime.now() - timedelta(hours=1)
        self.assertFalse(self.env['mos.delivery.order'].pod_for_token(token), 'an expired link finds nothing')

    def test_receiver_signs_on_the_phone_page_once(self):
        self._dispatched()
        path = self.delivery.action_pod_link()
        self.env.cr.flush()
        page = self.url_open(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Proof of delivery', page.text)
        self.assertIn(self.delivery.name, page.text)
        signature = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
        done = self.url_open(path, data={'receiver': 'Hana Tesfaye', 'signature': signature, 'note': 'Two bags torn'})
        self.assertEqual(done.status_code, 200)
        self.assertIn('Thank you', done.text)
        self.delivery.invalidate_recordset()
        self.assertEqual(self.delivery.pod_receiver, 'Hana Tesfaye')
        self.assertTrue(self.delivery.pod_signed_at)
        self.assertFalse(self.delivery.pod_token)
        again = self.url_open(path)
        self.assertEqual(again.status_code, 404)

    def test_a_signature_is_required(self):
        self._dispatched()
        self.delivery.action_pod_link()
        with self.assertRaises(UserError):
            self.delivery.record_pod('Someone', '')
        with self.assertRaises(UserError):
            self.delivery.record_pod('', 'AAAA')
