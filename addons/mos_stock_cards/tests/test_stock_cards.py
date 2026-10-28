from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestStockCards(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.user = new_test_user(env, login='xcardsuser', groups='mos_core.group_mos_user,stock.group_stock_user')
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.kg = env.ref('uom.product_uom_kgm')
        cls.product = env['product.product'].create({'name': 'Card wheat', 'type': 'consu', 'is_storable': True, 'uom_id': cls.kg.id})
        cls.bin_a = env['stock.location'].create({'name': 'CARD-A', 'location_id': cls.warehouse.lot_stock_id.id, 'usage': 'internal'})
        cls.bin_b = env['stock.location'].create({'name': 'CARD-B', 'location_id': cls.warehouse.lot_stock_id.id, 'usage': 'internal'})
        cls.supplier = env.ref('stock.stock_location_suppliers')
        cls.production = env['stock.location'].search([('usage', '=', 'production')], limit=1)
        cls.inventory = env['stock.location'].search([('usage', '=', 'inventory')], limit=1)

    def _move(self, source, dest, qty, when=None):
        move = self.env['stock.move'].create(
            {
                'product_id': self.product.id,
                'product_uom_qty': qty,
                'location_id': source.id,
                'location_dest_id': dest.id,
                'quantity': qty,
                'picked': True,
            }
        )
        move._action_done()
        if when:
            move.move_line_ids.date = when
            move.date = when
        return move

    def _card(self, mode, **kw):
        card = self.env['mos.stock.card'].with_user(self.user).create(
            {'mode': mode, 'warehouse_id': self.warehouse.id, 'product_id': self.product.id, 'date_from': '2000-01-01', 'date_to': fields.Date.today(), **kw}
        )
        card.action_search()
        return card

    def test_bin_card_follows_the_ledger(self):
        self._move(self.supplier, self.bin_a, 100.0)
        self._move(self.bin_a, self.production, 30.0)
        self._move(self.bin_a, self.bin_b, 20.0)
        self._move(self.bin_a, self.inventory, 5.0)
        card = self._card('bin', location_id=self.bin_a.id)
        self.assertEqual((card.opening, card.received, card.issued, card.closing), (0.0, 100.0, 55.0, 45.0))
        self.assertEqual([line.kind for line in card.line_ids], ['receipt', 'issue', 'transfer_out', 'adjustment'])
        self.assertEqual([line.balance for line in card.line_ids], [100.0, 70.0, 50.0, 45.0], 'a running balance')
        quant = self.env['stock.quant'].search([('product_id', '=', self.product.id), ('location_id', '=', self.bin_a.id)])
        self.assertEqual(card.closing, sum(quant.mapped('quantity')), 'the card ends where the ledger stands')
        self.assertEqual(card.ledger_gap, 0.0)
        self.assertIn('Bin card', card.display_name)

    def test_stock_without_a_movement_is_reported_not_hidden(self):
        self._move(self.supplier, self.bin_a, 100.0)
        self.env['stock.quant']._update_available_quantity(self.product, self.bin_a, 40.0)  # set directly, no movement behind it
        card = self._card('bin', location_id=self.bin_a.id)
        self.assertEqual(card.closing, 100.0)
        self.assertEqual(card.ledger_gap, 40.0)

    def test_opening_balance_carries_what_came_before(self):
        self._move(self.supplier, self.bin_a, 100.0, when='2026-01-10 08:00:00')
        self._move(self.bin_a, self.production, 40.0, when='2026-02-10 08:00:00')
        card = self._card('bin', location_id=self.bin_a.id, date_from='2026-02-01', date_to='2026-02-28')
        self.assertEqual((card.opening, card.received, card.issued, card.closing), (100.0, 0.0, 40.0, 60.0))
        self.assertEqual(len(card.line_ids), 1)

    def test_stock_card_transfer_between_bins_is_net_zero(self):
        self._move(self.supplier, self.bin_a, 100.0)
        self._move(self.bin_a, self.bin_b, 20.0)
        card = self._card('item')
        self.assertEqual((card.received, card.issued, card.closing), (100.0, 0.0, 100.0), 'a bin to bin move does not lower the warehouse')
        self.assertEqual([line.kind for line in card.line_ids], ['receipt', 'transfer_out', 'transfer_in'])
        self.assertEqual(card.line_ids[-1].balance, 100.0)
        self.assertEqual(card.current_stock, 100.0)
        stock_by_bin = {rec.location_id.name: rec.quantity for rec in card.bin_ids}
        self.assertEqual(stock_by_bin, {'CARD-A': 80.0, 'CARD-B': 20.0})
        self.assertEqual(card.bin_count, 2)

    def test_status_change_is_not_a_loss(self):
        quarantine = self.env['stock.location'].search([('mos_stock_status', '=', 'quarantine'), ('warehouse_id', '=', self.warehouse.id)], limit=1)
        if not quarantine:
            quarantine = self.env['stock.location'].create({'name': 'CARD-Q', 'location_id': self.warehouse.view_location_id.id, 'usage': 'internal', 'mos_stock_status': 'quarantine'})
        self._move(self.supplier, self.bin_a, 100.0)
        self._move(self.bin_a, quarantine, 10.0)
        card = self._card('item')
        self.assertEqual((card.issued, card.closing), (0.0, 100.0), 'a move to quarantine stays on hand (client defect N-14)')

    def test_filters_are_checked(self):
        card = self.env['mos.stock.card'].with_user(self.user).create({'mode': 'bin', 'warehouse_id': self.warehouse.id, 'product_id': self.product.id})
        with self.assertRaises(Exception):
            card.action_search()  # a bin card needs a bin
        card.write({'mode': 'item', 'date_from': '2026-02-02', 'date_to': '2026-02-01'})
        with self.assertRaises(Exception):
            card.action_search()

    def test_reset_and_export_and_print(self):
        self._move(self.supplier, self.bin_a, 10.0)
        card = self._card('item')
        self.assertTrue(card.searched)
        self.assertEqual(card.action_export()['res_model'], 'mos.stock.card.line')
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_stock_cards.report_stock_card', card.ids)
        self.assertIn(b'Card wheat', html)
        card.action_reset()
        self.assertFalse(card.searched)
        self.assertFalse(card.line_ids)
