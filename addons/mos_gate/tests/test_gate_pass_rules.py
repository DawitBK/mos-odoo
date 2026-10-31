from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestGatePassRules(TransactionCase):
    """FR-GATE-019: validity, rejection, asset tags and serials, estimated value, partial out and return, and stock items that need their document."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Pass mill', 'code': 'PASSM', 'warehouse_id': warehouse.id})

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('pguard', 'mos_gate.group_mos_gate_guard')
        cls.approver = user('papprover', 'mos_gate.group_mos_gate_pass_approver')
        cls.clerk = user('pclerk', 'mos_core.group_mos_user')
        cls.product = env['product.product'].create({'name': 'Scrap steel', 'type': 'consu', 'is_storable': True})

    def _pass(self, kind='returnable', lines=None, **extra):
        vals = {
            'site_id': self.site.id,
            'kind': kind,
            'carrier': 'Van',
            'purpose': 'Test',
            'expected_return': fields.Date.today() + timedelta(days=3),
            'line_ids': lines or [Command.create({'name': 'Generator', 'quantity': 4, 'asset_tag': 'AST-77', 'serial': 'SN-1', 'est_value': 1200.0})],
        }
        vals.update(extra)
        return self.env['mos.gate.pass'].with_user(self.clerk).create(vals)

    def test_a_pass_can_be_rejected_with_a_reason_by_an_approver_only(self):
        gp = self._pass()
        with self.assertRaises(UserError):
            gp.with_user(self.clerk).action_reject('no')
        with self.assertRaises(UserError):
            gp.with_user(self.approver).action_reject('  ')
        gp.with_user(self.approver).action_reject('Not our equipment')
        self.assertEqual((gp.state, gp.reject_reason), ('rejected', 'Not our equipment'))
        with self.assertRaises(UserError):
            gp.with_user(self.approver).action_approve()

    def test_a_pass_cannot_be_approved_or_used_after_its_validity(self):
        gp = self._pass(valid_until=fields.Date.today() - timedelta(days=1))
        with self.assertRaises(UserError):
            gp.with_user(self.approver).action_approve()
        ok = self._pass(valid_until=fields.Date.today() + timedelta(days=1))
        ok.with_user(self.approver).action_approve()
        ok.sudo().valid_until = fields.Date.today() - timedelta(days=1)
        with self.assertRaises(UserError):
            ok.with_user(self.guard).action_out()

    def test_the_daily_job_expires_approved_passes_nobody_used(self):
        gp = self._pass(valid_until=fields.Date.today() + timedelta(days=1))
        gp.with_user(self.approver).action_approve()
        self.assertEqual(self.env['mos.gate.pass']._cron_expire(), 0)
        gp.sudo().valid_until = fields.Date.today() - timedelta(days=1)
        self.assertEqual(self.env['mos.gate.pass']._cron_expire(), 1)
        self.assertEqual(gp.state, 'expired')

    def test_partial_out_and_partial_return(self):
        gp = self._pass()
        line = gp.line_ids
        gp.with_user(self.approver).action_approve()
        line.qty_out = 3
        gp.with_user(self.guard).action_out()
        self.assertEqual(line.qty_out, 3)
        line.qty_returned = 2
        gp.with_user(self.guard).action_return()
        self.assertEqual(gp.state, 'out', 'one is still out')
        line.qty_returned = 3
        gp.with_user(self.guard).action_return()
        self.assertEqual(gp.state, 'returned')

    def test_nothing_entered_means_all_goes_out_and_all_comes_back(self):
        gp = self._pass()
        gp.with_user(self.approver).action_approve()
        gp.with_user(self.guard).action_out()
        self.assertEqual(gp.line_ids.qty_out, 4)
        gp.with_user(self.guard).action_return()
        self.assertEqual(gp.line_ids.qty_returned, 4)
        self.assertEqual(gp.state, 'returned')

    def test_quantities_cannot_exceed_what_the_pass_or_the_exit_allows(self):
        gp = self._pass()
        with self.assertRaises(ValidationError):
            gp.line_ids.qty_out = 5
        gp.line_ids.qty_out = 2
        with self.assertRaises(ValidationError):
            gp.line_ids.qty_returned = 3

    def test_a_stock_item_on_a_final_pass_needs_its_posted_document(self):
        gp = self._pass(kind='final', expected_return=False, lines=[Command.create({'name': 'Scrap steel', 'quantity': 100, 'product_id': self.product.id})])
        gp.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            gp.with_user(self.guard).action_out()
        with patch.object(type(gp.line_ids), '_inventory_posted', return_value=True):
            gp.with_user(self.guard).action_out()
        self.assertEqual(gp.state, 'out')
