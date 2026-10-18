from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase, new_test_user


class ProcurementPlusSetup:
    @classmethod
    def _setup_plus(cls):
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLPP', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.buyer = user('xbuyer', 'mos_procurement.group_mos_procurement_user')
        cls.manager = user('xmanager', 'mos_procurement.group_mos_procurement_manager')
        cls.guard = user('xguard', 'mos_gate.group_mos_gate_guard')
        cls.verifier = user('xverifier', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('xanalyst', 'mos_lab.group_mos_lab_analyst')
        cls.labmgr = user('xlabmgr', 'mos_lab.group_mos_lab_manager')
        cls.store = user('xstore', 'mos_inbound.group_mos_storekeeper')
        cls.apclerk = user('xap', 'mos_payables.group_mos_ap_clerk')
        cls.requester = user('xrequester', 'mos_core.group_mos_user')
        kg = env.ref('uom.product_uom_kgm')
        cls.wheat = env['product.product'].create({'name': 'Plus wheat', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': 20.0})
        cls.service = env['product.product'].create({'name': 'Roller re-grinding', 'type': 'service', 'standard_price': 5000.0})
        cls.s1 = env['res.partner'].create({'name': 'Quote Supplier One', 'supplier_rank': 1})
        cls.s2 = env['res.partner'].create({'name': 'Quote Supplier Two', 'supplier_rank': 1})
        cls.s3 = env['res.partner'].create({'name': 'Quote Supplier Three', 'supplier_rank': 1})
        spec = env['mos.lab.spec'].create({'product_id': cls.wheat.id, 'line_ids': [Command.create({'name': 'Moisture', 'min_value': 0, 'max_value': 14})]})
        spec.action_approve()
        cls.line = spec.line_ids

    def _order(self, qty=40000.0, supplier=None):
        order = self.env['purchase.order'].with_user(self.buyer).create(
            {'partner_id': (supplier or self.s1).id, 'order_line': [Command.create({'product_id': self.wheat.id, 'product_qty': qty, 'price_unit': 20.0})]}
        )
        order.with_user(self.buyer).button_confirm()
        return order

    def _receive(self, order, plate, gross=50000.0, tare=15000.0, **visit_extra):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': order.partner_id.id, **visit_extra})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(gross)
        ticket.record_second(tare)
        ticket.with_user(self.verifier).verify_blind(gross, tare)
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(self.line, 12.0)
        insp.action_submit_results()
        insp.with_user(self.labmgr).decide('accept')
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        return visit


@tagged('post_install', '-at_install')
class TestProcurementPlus(ProcurementPlusSetup, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()

    # -- RFQ -------------------------------------------------------------------------------------------------------
    def _rfq(self, prices=(20.0, 19.0, 21.0)):
        rfq = self.env['mos.rfq'].with_user(self.buyer).create(
            {'site_id': self.site.id, 'invited_ids': [Command.set([self.s1.id, self.s2.id, self.s3.id])], 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': 10000.0})]}
        )
        rfq.action_send()
        line = rfq.line_ids
        quotes = []
        for partner, price in zip((self.s1, self.s2, self.s3), prices):
            if price:
                quotes.append(self.env['mos.rfq.quote'].with_user(self.buyer).create({'rfq_id': rfq.id, 'partner_id': partner.id, 'lead_days': 5, 'line_ids': [Command.create({'rfq_line_id': line.id, 'unit_price': price})]}))
        return rfq, quotes

    def test_rfq_award_makes_the_order_and_keeps_the_quotes(self):
        self.env['mos.sourcing.policy'].create({'amount_from': 100000, 'min_quotes': 3})
        rfq, (q1, q2, q3) = self._rfq()
        self.assertEqual((q1.total, q2.total, q3.total), (200000.0, 190000.0, 210000.0))
        with self.assertRaises(UserError):
            rfq.with_user(self.requester).action_award(q2)  # not a buyer
        with self.assertRaises(UserError):
            rfq.with_user(self.buyer).action_award(q1)  # not the lowest, no reason
        order = rfq.with_user(self.buyer).action_award(q2)
        self.assertEqual((rfq.state, rfq.order_id, order.partner_id), ('awarded', order, self.s2))
        self.assertEqual(order.order_line.price_unit, 19.0)
        self.assertEqual(len(order.mos_quote_ids), 3, 'the three offers travel with the order')
        order.with_user(self.buyer).button_confirm()  # the sourcing policy is satisfied by those offers
        self.assertEqual(order.state, 'purchase')
        with self.assertRaises(UserError):
            rfq.with_user(self.buyer).action_award(q3)  # already awarded
        with self.assertRaises(UserError):
            rfq.unlink()  # an awarded request is final

    def test_award_with_a_reason_when_not_lowest_or_too_few_offers(self):
        self.env['mos.sourcing.policy'].create({'amount_from': 100000, 'min_quotes': 3})
        rfq, (q1, q2) = self._rfq(prices=(20.0, 19.0, 0.0))
        with self.assertRaises(UserError):
            rfq.with_user(self.buyer).action_award(q1)  # two offers, policy wants three, and not lowest
        order = rfq.with_user(self.buyer).action_award(q1, 'Only supplier whose silo is within reach this week')
        self.assertEqual(order.partner_id, self.s1)
        self.assertTrue(order.mos_sole_source_reason)

    def test_incomplete_offers_and_uninvited_suppliers(self):
        rfq, _ = self._rfq()
        other = self.env['res.partner'].create({'name': 'Not invited', 'supplier_rank': 1})
        with self.assertRaises(ValidationError):
            self.env['mos.rfq.quote'].create({'rfq_id': rfq.id, 'partner_id': other.id})
        empty = self.env['mos.rfq'].create({'site_id': self.site.id, 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': 1})]})
        with self.assertRaises(UserError):
            empty.action_send()  # nobody invited
        self.s3.sudo().mos_supplier_state = 'blocked'
        blocked = self.env['mos.rfq'].create({'site_id': self.site.id, 'invited_ids': [Command.set([self.s3.id])], 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': 1})]})
        with self.assertRaises(UserError):
            blocked.action_send()
        self.s3.sudo().mos_supplier_state = 'candidate'

    # -- closure ---------------------------------------------------------------------------------------------------
    def test_order_closes_when_received_and_invoiced_or_short_by_a_manager(self):
        order = self._order(qty=40000.0)
        self._receive(order, 'X-1')  # 35 t of 40 t: 12.5 % short, more than the 5 % tolerance
        self.assertEqual(order._mos_close_if_complete(), self.env['purchase.order'])
        with self.assertRaises(UserError):
            order.with_user(self.buyer).action_close_short('not enough time')  # a buyer is not a manager
        with self.assertRaises(UserError):
            order.with_user(self.manager).action_close_short('')
        order.with_user(self.manager).action_close_short('Supplier will not deliver the last 5 t this season')
        self.assertEqual(order.mos_close_state, 'closed_short')
        self.assertFalse(order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')), 'the open receipt is cancelled')
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'X-1B', 'purchase_id': order.id, 'partner_id': self.s1.id})
        self.assertTrue(any('closed' in p for p in visit._mos_preflight()), 'a closed order takes no more deliveries')

    def test_a_received_and_invoiced_order_closes_by_itself(self):
        order = self._order(qty=36000.0)
        self._receive(order, 'X-2')  # 35 t of 36 t: inside the short-delivery tolerance
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create({'supplier_id': self.s1.id, 'supplier_ref': 'X2-1', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': 35000.0, 'unit_price': 20.0})
        self.assertEqual(order.mos_close_state, 'open')
        invoice.action_match()
        self.assertEqual(order.mos_close_state, 'closed')
        self.assertIn('invoiced', order.mos_close_reason)

    # -- bags ------------------------------------------------------------------------------------------------------
    def test_bagged_goods_are_paid_net_of_the_bags(self):
        bag = self.env['mos.bag.type'].create({'name': 'Jute 50 kg', 'tare_kg': 0.65})
        order = self._order()
        visit = self._receive(order, 'X-3', bag_type_id=bag.id, bag_count=700)
        self.assertEqual(visit.bag_tare_kg, 455.0)
        self.assertEqual(visit.inspection_id.payable_qty, 35000.0 - 455.0)
        with self.assertRaises(ValidationError):
            self.env['mos.bag.type'].create({'name': 'Negative', 'tare_kg': -1})

    # -- service receipts -------------------------------------------------------------------------------------------
    def test_service_receipts_confirm_non_stock_lines(self):
        request = self.env['mos.purchase.request'].with_user(self.requester).create(
            {'site_id': self.site.id, 'reason': 'Roller re-grinding', 'line_ids': [Command.create({'product_id': self.service.id, 'qty': 4, 'estimated_price': 5000.0})]}
        )
        request.action_submit()
        order = request.with_user(self.buyer).action_convert(self.s1)
        order.with_user(self.buyer).button_confirm()
        line = order.order_line
        self.assertTrue(line.mos_service)
        stranger = new_test_user(self.env, login='xstranger', groups='mos_core.group_mos_user')
        with self.assertRaises(UserError):
            self.env['mos.service.receipt'].with_user(stranger).create({'line_id': line.id, 'qty': 1, 'note': 'x'})
        receipt = self.env['mos.service.receipt'].with_user(self.requester).create({'line_id': line.id, 'qty': 3, 'note': 'Three rollers re-ground and fitted'})
        self.assertRegex(receipt.name, r'^SVC-\d{4}-\d{5}$')
        self.assertEqual(line.qty_received, 3.0)
        with self.assertRaises(UserError):
            self.env['mos.service.receipt'].with_user(self.requester).create({'line_id': line.id, 'qty': 2, 'note': 'too many'})
        with self.assertRaises(UserError):
            self.env['mos.service.receipt'].with_user(self.requester).create({'line_id': self._order().order_line.id, 'qty': 1, 'note': 'stock goes through the gate'})
        with self.assertRaises(UserError):
            receipt.write({'qty': 1})

    # -- registration and reports ---------------------------------------------------------------------------------
    def test_staff_register_a_supplier_in_one_go(self):
        wizard = self.env['mos.supplier.registration'].with_user(self.buyer).create(
            {
                'name': 'Gojjam Grain Traders',
                'vat': 'TIN-5550',
                'trade_licence': 'TL-2210',
                'licence_expires': fields.Date.today() + timedelta(days=300),
                'phone': '0911000000',
                'bank_name': 'Commercial Bank',
                'bank_account': '1000445566778',
                'self_billing': True,
            }
        )
        action = wizard.with_user(self.buyer).action_register()
        partner = self.env['res.partner'].browse(action['res_id'])
        self.assertEqual((partner.mos_supplier_state, partner.mos_self_billing), ('candidate', True))
        self.assertEqual(set(partner.mos_document_ids.mapped('doc_type')), {'trade_licence', 'tin'})
        self.assertEqual(partner.mos_bank_change_ids.new_account_masked, '*********6778', 'the account goes through the change control')
        with self.assertRaises(UserError):
            self.env['mos.supplier.registration'].create({'name': 'Duplicate', 'vat': 'TIN-5550', 'trade_licence': 'x', 'licence_expires': fields.Date.today()}).action_register()

    def test_reports_show_outstanding_deliveries_request_ageing_and_prices(self):
        order = self._order(qty=40000.0)
        self._receive(order, 'X-4')
        order.order_line.date_planned = fields.Datetime.now() - timedelta(days=3)
        request = self.env['mos.purchase.request'].with_user(self.requester).create({'site_id': self.site.id, 'reason': 'Report test', 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': 1000, 'estimated_price': 20.0})]})
        self.env.flush_all()
        self.env.invalidate_all()
        row = self.env['mos.po.outstanding'].search([('order_id', '=', order.id)])
        self.assertEqual((row.ordered, row.received, row.outstanding, row.days_late), (40000.0, 35000.0, 5000.0, 3))
        self.assertEqual(row.value_outstanding, 100000.0)
        age = self.env['mos.pr.ageing'].search([('request_id', '=', request.id)])
        self.assertEqual((age.state, age.age_days), ('draft', 0))
        price = self.env['mos.price.history'].search([('order_id', '=', order.id)])
        self.assertEqual((price.price, price.qty), (20.0, 40000.0))


@tagged('post_install', '-at_install')
class TestSupplierInvitation(ProcurementPlusSetup, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()

    def test_invited_supplier_registers_once_on_a_public_page(self):
        invite = self.env['mos.supplier.invite'].with_user(self.buyer).create({'name': 'Awash Mills Supplies', 'email': 'sales@awash.example'})
        path = invite.path()
        self.assertTrue(invite.token)
        self.env.cr.flush()
        page = self.url_open(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Register as a supplier', page.text)
        self.assertIn('Awash Mills Supplies', page.text)
        done = self.url_open(path, data={'name': 'Awash Mills Supplies PLC', 'vat': 'TIN-9090', 'email': 'sales@awash.example', 'phone': '0922', 'address': 'Adama', 'activity': 'Wheat and barley'})
        self.assertEqual(done.status_code, 200)
        self.assertIn('Thank you', done.text)
        invite.invalidate_recordset()
        self.assertEqual(invite.state, 'registered')
        self.assertEqual((invite.partner_id.name, invite.partner_id.mos_supplier_state, invite.partner_id.supplier_rank), ('Awash Mills Supplies PLC', 'candidate', 1))
        self.assertEqual(self.url_open(path).status_code, 404, 'the link works once')

    def test_form_errors_and_expiry(self):
        invite = self.env['mos.supplier.invite'].with_user(self.buyer).create({'name': 'Second supplier'})
        path = invite.path()
        self.env.cr.flush()
        bad = self.url_open(path, data={'name': 'No TIN'})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('TIN', bad.text)
        invite.invalidate_recordset()
        self.assertEqual(invite.state, 'open', 'a failed attempt leaves the link open')
        invite.sudo().expires_at = fields.Datetime.now() - timedelta(hours=1)
        self.env.cr.flush()
        self.assertEqual(self.url_open(path).status_code, 404)
        self.assertEqual(self.env['mos.supplier.invite']._cron_expire(), 0, 'the lookup already expired it')
