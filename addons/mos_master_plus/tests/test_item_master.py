from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestItemMaster(TransactionCase):
    """FR-MD-003: the item master carries every type, a shelf life, a packaging tare, and a code that is unique."""

    def _item(self, code, **extra):
        return self.env['product.template'].create({'name': f'Item {code}', 'default_code': code, **extra})

    def test_every_material_type_of_the_requirement_can_be_chosen(self):
        wanted = {'raw', 'packaging', 'semi', 'finished', 'byproduct', 'scrap', 'consumable', 'spare', 'nonstock'}
        offered = {key for key, _label in self.env['product.template']._fields['mos_material_type'].selection}
        self.assertLessEqual(wanted, offered)

    def test_an_item_code_is_unique_whatever_its_case_and_even_when_the_other_item_is_archived(self):
        first = self._item('UNQ-001')
        with self.assertRaises(ValidationError):
            self._item('unq-001')
        first.active = False
        with self.assertRaises(ValidationError):
            self._item('UNQ-001')
        self._item('UNQ-002')

    def test_shelf_life_and_packaging_tare_are_kept_and_the_shelf_life_cannot_be_negative(self):
        item = self._item('SHF-001', mos_shelf_life_days=180, mos_pack_tare_kg=0.12)
        self.assertEqual((item.mos_shelf_life_days, item.mos_pack_tare_kg), (180, 0.12))
        with self.assertRaises(ValidationError):
            item.mos_shelf_life_days = -1

    def test_a_lot_expires_after_the_shelf_life_unless_a_date_is_given(self):
        item = self._item('SHF-002', mos_shelf_life_days=30, tracking='lot')
        product = item.product_variant_id
        lot = self.env['stock.lot'].create({'name': 'LOT-SL-1', 'product_id': product.id, 'mos_mfg_date': '2026-10-01'})
        self.assertEqual(str(lot.mos_expiry_date), '2026-10-31')
        own = self.env['stock.lot'].create({'name': 'LOT-SL-2', 'product_id': product.id, 'mos_mfg_date': '2026-10-01', 'mos_expiry_date': '2026-12-01'})
        self.assertEqual(str(own.mos_expiry_date), '2026-12-01')
