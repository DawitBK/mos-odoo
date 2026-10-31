from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestStockIssue(TransactionCase):
    """FR-INV-006: goods issued to a department or scrapped, valued at the average cost, from available stock only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Issue mill', 'code': 'ISSM', 'warehouse_id': cls.warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.store = user('gi_store', 'mos_inbound.group_mos_storekeeper')
        cls.store2 = user('gi_store2', 'mos_inbound.group_mos_storekeeper')
        cls.manager = user('gi_manager', 'mos_inventory.group_mos_inventory_manager')
        cls.dept = env['mos.department'].create({'name': 'Maintenance'})
        cls.product = env['product.product'].create(
            {'name': 'Bearing', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_unit').id, 'standard_price': 40.0}
        )
        cls.stock = cls.warehouse.lot_stock_id
        quant = env['stock.quant'].with_context(inventory_mode=True).create({'product_id': cls.product.id, 'location_id': cls.stock.id, 'inventory_quantity': 20.0})
        quant.action_apply_inventory()
        cls.scrap_reason = env['mos.reason'].create({'domain': 'scrap', 'code': 'GI-DMG', 'name': 'Damaged in handling'})

    def _qty(self, location):
        return sum(self.env['stock.quant'].search([('product_id', '=', self.product.id), ('location_id', '=', location.id)]).mapped('quantity'))

    def _issue(self, qty=5.0, user=None, **extra):
        vals = {
            'site_id': self.site.id,
            'location_id': self.stock.id,
            'department_id': self.dept.id,
            'line_ids': [Command.create({'product_id': self.product.id, 'qty': qty})],
        }
        return self.env['mos.stock.issue'].with_user(user or self.store).create({**vals, **extra})

    def test_a_consumption_is_valued_at_average_cost_and_takes_the_stock_out(self):
        issue = self._issue(5.0)
        self.assertEqual(issue.amount, 200.0)
        issue.action_submit()
        self.assertEqual(issue.state, 'approved', 'no matrix row matches, so no approval is asked')
        issue.with_user(self.store2).action_issue()
        self.assertEqual(issue.state, 'issued')
        self.assertEqual(issue.issued_by_id, self.store2)
        self.assertEqual(self._qty(self.stock), 15.0)
        self.assertEqual(self._qty(self.env.ref('mos_inventory.location_consumed')), 5.0)
        self.assertTrue(issue.name.startswith('GI-'))

    def test_the_requester_cannot_post_an_issue_that_needed_no_approval(self):
        issue = self._issue(1.0)
        issue.action_submit()
        with self.assertRaises(UserError):
            issue.action_issue()

    def test_more_than_is_available_is_refused(self):
        issue = self._issue(25.0)
        with self.assertRaises(UserError):
            issue.action_submit()

    def test_stock_on_hold_cannot_be_issued(self):
        held = self.env['stock.location'].create({'name': 'Held', 'usage': 'internal', 'location_id': self.warehouse.view_location_id.id, 'mos_stock_status': 'quarantine'})
        with self.assertRaises(ValidationError):
            self._issue(1.0, location_id=held.id)

    def test_a_department_is_needed_for_consumption_and_a_reason_for_scrap(self):
        with self.assertRaises(ValidationError):
            self._issue(1.0, department_id=False)
        with self.assertRaises(ValidationError):
            self._issue(1.0, kind='scrap', department_id=False)

    def test_scrap_goes_to_the_scrap_location_and_follows_the_matrix(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.stock.issue', 'amount_from': 0.0, 'group_id': self.env.ref('mos_inventory.group_mos_inventory_manager').id})
        issue = self._issue(2.0, kind='scrap', reason_id=self.scrap_reason.id)
        issue.action_submit()
        self.assertEqual(issue.state, 'pending')
        self.assertTrue(issue.mos_approval_request_id)
        issue.mos_approval_request_id.current_step_id.with_user(self.manager).action_approve()
        self.assertEqual(issue.state, 'approved')
        issue.with_user(self.store2).action_issue()
        self.assertEqual(self._qty(self.env.ref('mos_inventory.location_scrapped')), 2.0)

    def test_scrap_without_a_matrix_row_posts_to_the_scrap_location(self):
        issue = self._issue(2.0, kind='scrap', reason_id=self.scrap_reason.id)
        issue.action_submit()
        issue.with_user(self.store2).action_issue()
        self.assertEqual(self._qty(self.env.ref('mos_inventory.location_scrapped')), 2.0)

    def test_an_issued_document_cannot_be_changed_or_cancelled(self):
        issue = self._issue(1.0)
        issue.action_submit()
        issue.with_user(self.store2).action_issue()
        with self.assertRaises(UserError):
            issue.action_cancel()
        with self.assertRaises(UserError):
            issue.write({'note': 'edited after posting'})
