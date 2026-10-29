from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPageChrome(TransactionCase):
    def _form(self, model, view_xmlid=None):
        view_id = self.env.ref(view_xmlid).id if view_xmlid else None
        return etree.fromstring(self.env[model].get_view(view_id, 'form')['arch'])

    def test_a_mos_form_has_the_page_header_the_cards_and_the_action_bar(self):
        arch = self._form('mos.lab.inspection')
        self.assertTrue(arch.xpath('//div[hasclass("mos-page-head")]//h2[text()="QC Inspection"]'))
        titles = arch.xpath('//div[hasclass("mos-section")]/div[hasclass("mos-section-title")]/span/text()')
        self.assertIn('Quality parameters', titles)
        self.assertEqual(arch.xpath('//header/button'), [], 'the buttons are not at the top any more')
        bar_buttons = arch.xpath('//div[hasclass("mos-action-bar")]/button/@name')
        self.assertIn('action_submit_results', bar_buttons)

    def test_the_primary_button_comes_last_in_the_bar(self):
        arch = self._form('mos.purchase.request')
        classes = arch.xpath('//div[hasclass("mos-action-bar")]/button/@class')
        self.assertTrue(classes)
        self.assertIn('btn-primary', classes[-1])

    def test_a_card_keeps_the_visibility_of_its_title(self):
        arch = self._form('mos.lab.inspection')
        card = arch.xpath('//div[hasclass("mos-section")][div/span="Certificates of analysis"]')
        self.assertTrue(card)
        self.assertTrue(card[0].get('invisible'), 'the card hides itself when it has nothing to show')

    def test_a_form_that_has_its_own_header_is_left_alone(self):
        arch = self._form('mos.gate.visit', 'mos_deliveries.view_mos_delivery_entry_form')
        self.assertEqual(len(arch.xpath('//div[hasclass("mos-page-head")]')), 1)

    def test_the_purchase_order_gets_the_page_but_keeps_its_buttons_in_the_header(self):
        arch = self._form('purchase.order')
        self.assertTrue(arch.xpath('//div[hasclass("mos-page-head")]//h2[text()="Purchase Order"]'))
        self.assertTrue(arch.xpath('//header/button'), "Odoo's own order keeps its buttons where its flows expect them")
        self.assertTrue(arch.xpath('//div[hasclass("mos-section")][div/span="Part 1: Purchase order header"]'))

    def test_every_mos_form_gets_the_header(self):
        for model in self.env.registry:
            if not model.startswith('mos.') or self.env[model]._transient:
                continue
            arch = self._form(model)
            if arch.find('.//sheet') is None:
                continue
            with self.subTest(model=model):
                self.assertTrue(arch.xpath('//div[hasclass("mos-page-head")]'), model)
