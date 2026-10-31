from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestBirr(TransactionCase):
    """A new MOS database belongs to an Ethiopian mill: its money is birr from the start."""

    def test_a_new_installation_keeps_its_money_in_birr(self):
        company = self.env.ref('base.main_company')
        self.assertEqual(company.currency_id.name, 'ETB')
        self.assertEqual(set(self.env['product.pricelist'].with_context(active_test=False).search([]).mapped('currency_id.name')), {'ETB'})
