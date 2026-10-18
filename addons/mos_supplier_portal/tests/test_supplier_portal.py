import re
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from odoo.addons.mos_procurement_plus.tests.test_procurement_plus import ProcurementPlusSetup

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


@tagged('post_install', '-at_install')
class TestSupplierPortal(ProcurementPlusSetup, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_plus()
        cls.s1.write({'email': 'one@supplier.test'})
        cls.s2.write({'email': 'two@supplier.test'})
        portal = cls.env.ref('base.group_portal')
        cls.p1 = cls.env['res.users'].with_context(no_reset_password=True).create({'name': 'Supplier One', 'login': 'portal1', 'email': 'one@supplier.test', 'partner_id': cls.s1.id, 'group_ids': [Command.set([portal.id])], 'password': 'Portal-pass-1'})
        cls.p2 = cls.env['res.users'].with_context(no_reset_password=True).create({'name': 'Supplier Two', 'login': 'portal2', 'email': 'two@supplier.test', 'partner_id': cls.s2.id, 'group_ids': [Command.set([portal.id])], 'password': 'Portal-pass-2'})
        cls.o1 = cls._order(cls)  # supplier one
        cls.o2 = cls._order(cls, supplier=cls.s2)

    def _get(self, path, **kw):
        return self.url_open(path, allow_redirects=False, **kw)

    def _post(self, path, data, files=None):
        data = {'csrf_token': self.csrf_token(), **data}
        return self.url_open(path, data=data, files=files, allow_redirects=False)

    ROOT = '/mos/supplier/portal'

    def test_a_staff_member_or_a_stranger_does_not_get_the_supplier_pages(self):
        self.authenticate('xbuyer', 'xbuyer')
        self.assertEqual(self._get(self.ROOT).status_code, 403, 'the portal is for suppliers')
        self.authenticate(None, None)
        self.assertIn(self._get(self.ROOT).status_code, (303, 401, 403), 'a stranger is sent to sign in')

    def test_a_supplier_sees_only_its_own_orders(self):
        self.authenticate('portal1', 'Portal-pass-1')
        listing = self._get(f'{self.ROOT}/orders')
        self.assertEqual(listing.status_code, 200)
        self.assertIn(self.o1.name, listing.text)
        self.assertNotIn(self.o2.name, listing.text)
        self.assertEqual(self._get(f'{self.ROOT}/orders/{self.o1.id}').status_code, 200)
        self.assertEqual(self._get(f'{self.ROOT}/orders/{self.o2.id}').status_code, 404, "another supplier's order does not exist for you")
        home = self._get(self.ROOT)
        self.assertIn('Orders placed with you', home.text)
        self.authenticate('portal2', 'Portal-pass-2')
        self.assertIn(self.o2.name, self._get(f'{self.ROOT}/orders').text)
        self.assertEqual(self._get(f'{self.ROOT}/orders/{self.o1.id}').status_code, 404)

    def test_a_supplier_promises_delivery_dates(self):
        self.authenticate('portal1', 'Portal-pass-1')
        line = self.o1.order_line
        promised = fields.Date.today() + timedelta(days=9)
        done = self._post(f'{self.ROOT}/orders/{self.o1.id}', {f'promise_{line.id}': str(promised), f'note_{line.id}': 'Truck leaves Adama Monday'})
        self.assertEqual(done.status_code, 303)
        line.invalidate_recordset()
        self.assertEqual((line.mos_promise_date, line.mos_promise_note), (promised, 'Truck leaves Adama Monday'))
        self.assertIn(str(promised), self._get(f'{self.ROOT}/orders/{self.o1.id}').text)
        self.assertEqual(self._post(f'{self.ROOT}/orders/{self.o1.id}', {f'promise_{line.id}': 'not a date'}).status_code, 400)
        self.assertEqual(self._post(f'{self.ROOT}/orders/{self.o1.id}', {f'promise_{line.id}': str(fields.Date.today() - timedelta(days=30))}).status_code, 400)
        self.assertEqual(self._post(f'{self.ROOT}/orders/{self.o2.id}', {'x': '1'}).status_code, 404, "cannot touch another supplier's order")
        self.assertFalse(self.o2.order_line.mos_promise_date)

    def _rfq(self, invited, deadline_days=5):
        rfq = self.env['mos.rfq'].with_user(self.buyer).create(
            {'site_id': self.site.id, 'deadline': fields.Date.today() + timedelta(days=deadline_days), 'invited_ids': [Command.set(invited.ids)], 'line_ids': [Command.create({'product_id': self.wheat.id, 'qty': 10000.0})]}
        )
        rfq.action_send()
        return rfq

    def test_an_invited_supplier_answers_a_request_and_only_an_invited_one(self):
        rfq = self._rfq(self.s1 | self.s2)
        line = rfq.line_ids
        self.authenticate('portal1', 'Portal-pass-1')
        self.assertIn(rfq.name, self._get(f'{self.ROOT}/rfqs').text)
        page = self._get(f'{self.ROOT}/rfqs/{rfq.id}')
        self.assertEqual(page.status_code, 200)
        self.assertIn(f'name="price_{line.id}"', page.text)
        done = self._post(f'{self.ROOT}/rfqs/{rfq.id}', {f'price_{line.id}': '21.50', 'lead_days': '6', 'valid_until': str(fields.Date.today() + timedelta(days=20)), 'note': 'Ex works'})
        self.assertEqual(done.status_code, 303)
        quote = rfq.quote_ids.filtered(lambda q: q.partner_id == self.s1)
        self.assertEqual((len(quote), quote.lead_days, quote.line_ids.unit_price, quote.total), (1, 6, 21.5, 215000.0))
        self._post(f'{self.ROOT}/rfqs/{rfq.id}', {f'price_{line.id}': '20.00', 'lead_days': '4'})
        self.assertEqual((len(rfq.quote_ids.filtered(lambda q: q.partner_id == self.s1)), quote.line_ids.unit_price), (1, 20.0), 'a second answer replaces the first')
        self.assertEqual(self._post(f'{self.ROOT}/rfqs/{rfq.id}', {f'price_{line.id}': '-1'}).status_code, 400)
        self.assertEqual(self._post(f'{self.ROOT}/rfqs/{rfq.id}', {'lead_days': '3'}).status_code, 400, 'no price at all')
        only_two = self._rfq(self.s2)
        self.assertEqual(self._get(f'{self.ROOT}/rfqs/{only_two.id}').status_code, 404, 'not invited')
        self.assertEqual(self._post(f'{self.ROOT}/rfqs/{only_two.id}', {f'price_{only_two.line_ids.id}': '10'}).status_code, 404)
        self.assertFalse(only_two.quote_ids)

    def test_a_closed_request_takes_no_more_offers(self):
        rfq = self._rfq(self.s1)
        rfq.sudo().deadline = fields.Date.today() - timedelta(days=1)
        self.authenticate('portal1', 'Portal-pass-1')
        self.assertIn('closed', self._get(f'{self.ROOT}/rfqs/{rfq.id}').text)
        self.assertEqual(self._post(f'{self.ROOT}/rfqs/{rfq.id}', {f'price_{rfq.line_ids.id}': '10'}).status_code, 400)
        self.assertFalse(rfq.quote_ids)

    def test_a_supplier_sends_an_invoice_the_clerk_reviews(self):
        self.authenticate('portal1', 'Portal-pass-1')
        path = f'{self.ROOT}/invoices'
        sent = self._post(path, {'supplier_ref': 'INV-77', 'order_id': str(self.o1.id), 'amount': '700000'}, files={'file': ('inv77.pdf', PDF, 'application/pdf')})
        self.assertEqual(sent.status_code, 303)
        self.assertIn('saved=1', sent.headers['Location'])
        sub = self.env['mos.supplier.submission'].search([('supplier_id', '=', self.s1.id)])
        self.assertEqual((sub.supplier_ref, sub.amount, sub.order_id, sub.state), ('INV-77', 700000.0, self.o1, 'new'))
        self.assertEqual(len(sub.attachment_ids), 1)
        self.assertEqual(len(sub.attachment_ids.sha256), 64, 'kept with its fingerprint')
        self.assertIn('INV-77', self._get(path).text)
        again = self._post(path, {'supplier_ref': 'INV-77', 'amount': '1'}, files={'file': ('b.pdf', PDF + b'x', 'application/pdf')})
        self.assertIn('already', again.headers['Location'].lower().replace('+', ' ').replace('%20', ' '), 'the same number twice is refused plainly')
        fake = self._post(path, {'supplier_ref': 'INV-78', 'amount': '5'}, files={'file': ('inv78.pdf', b'this is not a pdf', 'application/pdf')})
        self.assertIn('error=', fake.headers['Location'], 'the file is checked by its content')
        stolen = self._post(path, {'supplier_ref': 'INV-79', 'order_id': str(self.o2.id), 'amount': '5'}, files={'file': ('c.pdf', PDF + b'y', 'application/pdf')})
        self.assertIn('error=', stolen.headers['Location'], "not another supplier's order")
        self.assertEqual(self.env['mos.supplier.submission'].search_count([('supplier_id', '=', self.s1.id)]), 1)
        self.authenticate('portal2', 'Portal-pass-2')
        self.assertNotIn('INV-77', self._get(path).text, "one supplier never sees another's submissions")
        with self.assertRaises(UserError):
            sub.with_user(self.requester).action_review()  # not the payables clerk
        with self.assertRaises(UserError):
            sub.with_user(self.apclerk).action_reject()  # no reason
        sub.with_user(self.apclerk).review_note = 'Wrong tax number'
        sub.with_user(self.apclerk).action_reject()
        self.assertEqual((sub.state, sub.review_note, sub.reviewed_by_id), ('rejected', 'Wrong tax number', self.apclerk))
        with self.assertRaises(UserError):
            sub.with_user(self.apclerk).action_review()  # decided already
        self.authenticate('portal1', 'Portal-pass-1')
        page = self._get(path).text
        self.assertIn('Wrong tax number', page, 'the supplier is told why')

    def test_the_statement_shows_the_suppliers_own_movements(self):
        self.authenticate('portal1', 'Portal-pass-1')
        page = self._get(f'{self.ROOT}/statement')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Closing balance', page.text)

    def test_a_manager_invites_a_supplier_to_the_portal(self):
        newcomer = self.env['res.partner'].create({'name': 'Newcomer Grain', 'supplier_rank': 1, 'email': 'Sales@Newcomer.Test'})
        with self.assertRaises(UserError):
            newcomer.with_user(self.buyer).mos_portal_invite()  # a buyer is not a manager
        url = newcomer.with_user(self.manager).mos_portal_invite()
        self.assertRegex(url, r'/web/(signup|reset_password)')
        user = self.env['res.users'].search([('login', '=', 'sales@newcomer.test')])
        self.assertEqual((user.partner_id, user.share), (newcomer, True))
        self.assertEqual(newcomer.with_user(self.manager).mos_portal_invite().split('?')[0], url.split('?')[0], 'inviting again reuses the sign-in')
        noemail = self.env['res.partner'].create({'name': 'No Mail Co', 'supplier_rank': 1})
        with self.assertRaises(UserError):
            noemail.with_user(self.manager).mos_portal_invite()
        blocked = self.env['res.partner'].create({'name': 'Blocked Co', 'supplier_rank': 1, 'email': 'b@blocked.test'})
        blocked.sudo().mos_supplier_state = 'blocked'
        with self.assertRaises(UserError):
            blocked.with_user(self.manager).mos_portal_invite()
        customer = self.env['res.partner'].create({'name': 'A customer', 'email': 'c@customer.test'})
        with self.assertRaises(UserError):
            customer.with_user(self.manager).mos_portal_invite()
