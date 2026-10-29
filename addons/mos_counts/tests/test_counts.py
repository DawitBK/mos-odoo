import base64

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestCounts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Count mill', 'code': 'CNTM', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.store = user('cstore', 'mos_inbound.group_mos_storekeeper')
        cls.store2 = user('cstore2', 'mos_inbound.group_mos_storekeeper')
        cls.manager = user('cmanager', 'mos_inventory.group_mos_inventory_manager')
        kg = env.ref('uom.product_uom_kgm')
        cls.location = env['stock.location'].create({'name': 'COUNT-BIN', 'location_id': warehouse.lot_stock_id.id, 'usage': 'internal'})
        cls.flour = env['product.product'].create({'name': 'Count flour', 'default_code': 'CF-1', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id})
        cls.bran = env['product.product'].create({'name': 'Count bran', 'default_code': 'CB-1', 'type': 'consu', 'is_storable': True, 'uom_id': kg.id})
        for product, qty in ((cls.flour, 1000.0), (cls.bran, 500.0)):
            quant = env['stock.quant'].with_context(inventory_mode=True).create({'product_id': product.id, 'location_id': cls.location.id, 'inventory_quantity': qty})
            quant.action_apply_inventory()
        cls.icp = env['ir.config_parameter'].sudo()

    def _count(self):
        count = self.env['mos.cycle.count'].with_user(self.store).create({'site_id': self.site.id, 'location_id': self.location.id})
        count.action_start()
        return count

    def _line(self, count, product):
        return count.line_ids.filtered(lambda l: l.product_id == product)

    # -- blind -----------------------------------------------------------------------------------------------------------
    def test_a_count_is_blind_unless_the_setting_says_otherwise(self):
        count = self._count()
        self.assertTrue(count.blind)
        self.assertTrue(count.hide_book, 'the counter does not see the book quantity while counting')
        count.line_ids.with_user(self.store).write({'counted_qty': 1.0})
        self.icp.set_str('mos_counts.blind_by_default', 'False')
        self.assertFalse(self._count().blind)

    # -- the count sheet -------------------------------------------------------------------------------------------------
    def test_the_count_sheet_prints_without_the_book_quantity_when_blind(self):
        count = self._count()
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_counts.report_count_sheet', count.ids)
        self.assertIn(b'CF-1', html)
        self.assertIn(b'Count flour', html)
        self.assertNotIn(b'Book quantity', html, 'a blind sheet hides the book quantity')
        count.blind = False
        html, _ = self.env['ir.actions.report']._render_qweb_html('mos_counts.report_count_sheet', count.ids)
        self.assertIn(b'Book quantity', html)

    # -- import from a spreadsheet ----------------------------------------------------------------------------------------
    def _import(self, count, text, name='counts.csv'):
        wizard = self.env['mos.count.import'].with_user(self.store).create({'count_id': count.id, 'file': base64.b64encode(text.encode()).decode(), 'filename': name})
        return wizard.action_import()

    def test_counts_are_imported_by_code_or_name(self):
        count = self._count()
        self._import(count, 'Item code,Counted quantity\nCF-1,990\nCount bran,505\n')
        self.assertEqual((self._line(count, self.flour).counted_qty, self._line(count, self.bran).counted_qty), (990.0, 505.0))
        self.assertTrue(all(line.counted for line in count.line_ids))

    def test_a_semicolon_file_and_a_workbook_are_read_too(self):
        from io import BytesIO

        from openpyxl import Workbook

        count = self._count()
        self._import(count, 'code;qty\nCF-1;1000\nCB-1;500\n')
        self.assertEqual(self._line(count, self.flour).counted_qty, 1000.0)
        book = Workbook()
        sheet = book.active
        sheet.append(['Item', 'Physical quantity'])
        sheet.append(['CF-1', 998.5])
        buf = BytesIO()
        book.save(buf)
        wizard = self.env['mos.count.import'].with_user(self.store).create({'count_id': count.id, 'file': base64.b64encode(buf.getvalue()).decode(), 'filename': 'counts.xlsx'})
        wizard.action_import()
        self.assertEqual(self._line(count, self.flour).counted_qty, 998.5)

    def test_nothing_is_imported_when_a_row_is_wrong(self):
        count = self._count()
        with self.assertRaises(UserError) as caught:
            self._import(count, 'code,qty\nCF-1,990\nNOPE,5\nCB-1,abc\n')
        message = str(caught.exception)
        self.assertIn('NOPE'.lower(), message.lower())
        self.assertIn('abc', message)
        self.assertFalse(any(line.counted for line in count.line_ids), 'all or nothing')
        with self.assertRaises(UserError):
            self._import(count, 'foo,bar\n1,2\n')  # no recognisable columns

    # -- recount by somebody else ----------------------------------------------------------------------------------------
    def test_a_large_variance_is_counted_again_by_somebody_else(self):
        self.icp.set_str('mos_counts.recount_pct', '2')
        count = self._count()
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 900.0})  # 10% short
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 500.0})
        notice = count.with_user(self.store).action_send_review()
        self.assertEqual(notice['params']['type'], 'warning')
        flour = self._line(count, self.flour)
        self.assertTrue(flour.recount and not flour.counted)
        self.assertEqual(count.state, 'counting')
        with self.assertRaises(UserError):
            flour.with_user(self.store).write({'counted_qty': 905.0})  # the same person cannot recount
        flour.with_user(self.store2).write({'counted_qty': 901.0})
        self.assertTrue(flour.recounted and not flour.recount)
        self.assertEqual(flour.previous_qty, 900.0)
        self.assertEqual(flour.first_counter_id, self.store)
        count.with_user(self.store).action_send_review()  # no second forced recount
        self.assertEqual(count.state, 'review')

    def test_the_recount_limit_is_off_by_default(self):
        count = self._count()
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 100.0})
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 500.0})
        count.with_user(self.store).action_send_review()
        self.assertEqual(count.state, 'review')

    # -- review -----------------------------------------------------------------------------------------------------------
    def test_review_is_required_only_when_the_setting_is_on(self):
        count = self._count()
        count.line_ids.with_user(self.store).write({'counted_qty': 1.0})
        count.with_user(self.store).action_submit()  # off by default: submits as before
        self.assertEqual(count.state, 'submitted')
        self.icp.set_str('mos_counts.require_review', 'True')
        other = self._count()
        other.line_ids.with_user(self.store).write({'counted_qty': 1.0})
        with self.assertRaises(UserError):
            other.with_user(self.store).action_submit()

    def test_a_reviewer_approves_and_the_adjustment_follows(self):
        self.icp.set_str('mos_counts.require_review', 'True')
        count = self._count()
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 950.0})
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 500.0})
        count.with_user(self.store).action_send_review()
        self.assertEqual(count.state, 'review')
        with self.assertRaises(UserError):
            count.with_user(self.store).action_review_approve()  # the counter cannot review
        count.with_user(self.manager).action_review_approve()
        self.assertEqual((count.state, count.reviewer_id), ('submitted', self.manager))
        self.assertTrue(count.adjustment_id, 'the difference became an adjustment')

    def test_asking_for_changes_sends_chosen_lines_back(self):
        self.icp.set_str('mos_counts.require_review', 'True')
        count = self._count()
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 950.0})
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 500.0})
        count.with_user(self.store).action_send_review()
        dialog = self.env['mos.count.changes'].with_user(self.manager).with_context(default_count_id=count.id).create({'note': 'Check the second pallet'})
        self.assertEqual(dialog.line_ids, self._line(count, self.flour), 'the lines with a variance are chosen by default')
        with self.assertRaises(UserError):
            self.env['mos.count.changes'].with_user(self.store).create({'count_id': count.id, 'note': 'x', 'line_ids': [Command.set(count.line_ids.ids)]}).action_confirm()
        dialog.action_confirm()
        self.assertEqual((count.state, count.review_note), ('counting', 'Check the second pallet'))
        flour = self._line(count, self.flour)
        self.assertTrue(flour.recount and not flour.counted)
        self.assertTrue(self._line(count, self.bran).counted, 'the other line stays counted')
        flour.with_user(self.store2).write({'counted_qty': 975.0})
        count.with_user(self.store).action_send_review()
        count.with_user(self.manager).action_review_approve()
        self.assertEqual(count.state, 'submitted')

    def test_a_reason_is_required_to_ask_for_changes(self):
        self.icp.set_str('mos_counts.require_review', 'True')
        count = self._count()
        count.line_ids.with_user(self.store).write({'counted_qty': 1.0})
        count.with_user(self.store).action_send_review()
        with self.assertRaises(UserError):
            count.with_user(self.manager).action_request_changes('  ', count.line_ids)

    def test_the_summary_counts_matches_and_variances(self):
        count = self._count()
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 1000.0})
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 450.0})
        self.assertEqual((count.matched_lines, count.variance_lines), (1, 1))

    # -- the count page and the adjustment page (client screens 13-15) ----------------------------------------------------------
    def test_the_count_adds_up_matched_and_variance_lines_and_one_total_for_each_unit(self):
        pieces = self.env['product.product'].create({'name': 'Count bags', 'default_code': 'CB-9', 'type': 'consu', 'is_storable': True, 'uom_id': self.env.ref('uom.product_uom_unit').id})
        self.env['stock.quant'].with_context(inventory_mode=True).create({'product_id': pieces.id, 'location_id': self.location.id, 'inventory_quantity': 100.0}).action_apply_inventory()
        count = self._count()
        self.assertEqual((count.item_count, count.matched_lines, count.variance_lines), (3, 0, 0))
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 1200.0, 'remark': 'more than the book'})
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 500.0})
        self._line(count, pieces).with_user(self.store).write({'counted_qty': 80.0})
        self.assertEqual((count.matched_lines, count.variance_lines), (1, 2))
        self.assertEqual((round(count.matched_pct), round(count.variance_pct)), (33, 67))
        self.assertEqual([self._line(count, p).status for p in (self.flour, self.bran, pieces)], ['variance', 'matched', 'variance'])
        self.assertIn('1,700.0', count.physical_totals, 'kilograms add up with kilograms')
        self.assertIn('80.0', count.physical_totals)
        self.assertNotIn('1,780', count.physical_totals, 'and never with pieces')
        self.assertIn('+200.0', count.difference_totals)
        self.assertIn('-20.0', count.difference_totals)
        self.assertEqual(self._line(count, self.flour).bin_id, self.location)
        self.assertEqual(count.warehouse_id, self.location.warehouse_id)

    def test_a_remark_goes_on_a_line_only_while_the_sheet_is_open(self):
        count = self._count()
        line = self._line(count, self.flour)
        line.with_user(self.store).write({'remark': 'check the corner'})
        count.write({'state': 'submitted'})
        with self.assertRaises(UserError):
            line.write({'remark': 'late'})

    def test_the_adjustment_adds_up_the_increase_and_the_decrease_for_each_unit(self):
        count = self._count()
        self._line(count, self.flour).with_user(self.store).write({'counted_qty': 1200.0})
        self._line(count, self.bran).with_user(self.store).write({'counted_qty': 450.0})
        adjustment = self.env['mos.stock.adjustment'].create(
            {
                'site_id': self.site.id,
                'location_id': self.location.id,
                'reason_id': self.env['mos.reason'].create({'domain': 'adjustment', 'code': 'CNT-T', 'name': 'Count test'}).id,
                'line_ids': [Command.create({'product_id': self.flour.id, 'new_qty': 1200.0}), Command.create({'product_id': self.bran.id, 'new_qty': 450.0})],
            }
        )
        self.assertEqual((adjustment.item_count, adjustment.adjustment_type), (2, 'mixed'))
        self.assertIn('+200.0', adjustment.increase_totals)
        self.assertIn('50.0', adjustment.decrease_totals)
        self.assertIn('+150.0', adjustment.net_totals)
        self.assertIn('1,500.0', adjustment.before_totals)
        self.assertIn('1,650.0', adjustment.after_totals)
