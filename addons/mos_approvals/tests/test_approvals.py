from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestApprovals(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.buyer = new_test_user(env, login='ap_buyer', groups='purchase.group_purchase_user,mos_core.group_mos_user')
        cls.dept1 = new_test_user(env, login='ap_dept1', groups='mos_approvals.group_mos_dept_manager,purchase.group_purchase_user')
        cls.dept2 = new_test_user(env, login='ap_dept2', groups='mos_approvals.group_mos_dept_manager')
        cls.fin = new_test_user(env, login='ap_fin', groups='mos_approvals.group_mos_finance_manager')
        cls.gm = new_test_user(env, login='ap_gm', groups='mos_approvals.group_mos_general_manager')
        cls.supplier = env['res.partner'].create({'name': 'Grain Co', 'supplier_rank': 1})
        cls.product = env['product.product'].create({'name': 'Wheat', 'type': 'consu', 'is_storable': True, 'standard_price': 1})
        g = env.ref
        Rule = env['mos.approval.rule']
        # 0 to 50,000: the department manager. Above 50,000: the department manager, then finance (who may escalate to the GM).
        Rule.create({'doc_type': 'purchase.order', 'sequence': 10, 'amount_from': 0, 'group_id': g('mos_approvals.group_mos_dept_manager').id, 'sla_hours': 4})
        Rule.create({'doc_type': 'purchase.order', 'sequence': 20, 'amount_from': 50000.01, 'group_id': g('mos_approvals.group_mos_finance_manager').id, 'fallback_group_id': g('mos_approvals.group_mos_general_manager').id})

    def _po(self, amount, user=None):
        user = user or self.buyer
        return self.env['purchase.order'].with_user(user).create(
            {'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 1, 'price_unit': amount})]}
        )

    def _step(self, po, user):
        return po.mos_approval_request_id.step_ids.filtered(lambda s: s.state == 'pending').with_user(user)

    def test_small_order_needs_one_approval_and_cannot_be_confirmed_before(self):
        po = self._po(20000)
        self.assertTrue(po._mos_approval_required())
        with self.assertRaises(UserError):
            po.with_user(self.buyer).button_confirm()
        po.action_mos_submit_approval()
        self.assertEqual(po.mos_approval_state, 'pending')
        request = po.mos_approval_request_id
        self.assertEqual(len(request.step_ids), 1)
        self._step(po, self.dept1).action_approve('ok')
        self.assertEqual(po.mos_approval_state, 'approved')
        po.with_user(self.buyer).button_confirm()
        self.assertEqual(po.state, 'purchase')

    def test_large_order_runs_stages_in_order(self):
        po = self._po(80000)
        po.action_mos_submit_approval()
        request = po.mos_approval_request_id
        self.assertEqual(request.step_ids.mapped('sequence'), [1, 2])
        with self.assertRaises(AccessError):
            self._step(po, self.fin).action_approve()  # not their turn: stage 2 is still waiting, stage 1 is pending for dept
        self._step(po, self.dept1).action_approve()
        self.assertEqual(po.mos_approval_state, 'pending')
        self._step(po, self.fin).action_approve()
        self.assertEqual(po.mos_approval_state, 'approved')
        self.assertEqual(request.state, 'approved')

    def test_nobody_approves_their_own_document_or_two_stages(self):
        # The submitter is also a department manager: segregation of duties leaves nobody for stage 1.
        manager_buyer = new_test_user(self.env, login='ap_both', groups='mos_approvals.group_mos_dept_manager,purchase.group_purchase_user')
        self.dept1.group_ids -= self.env.ref('mos_approvals.group_mos_dept_manager')
        self.dept2.group_ids -= self.env.ref('mos_approvals.group_mos_dept_manager')
        po = self._po(10000, manager_buyer)
        with self.assertRaises(UserError):
            po.with_user(manager_buyer).action_mos_submit_approval()
        self.assertEqual(po.mos_approval_state, 'none')
        self.assertFalse(self.env['mos.approval.request'].search([('res_id', '=', po.id)]))

    def test_same_person_cannot_approve_two_stages(self):
        both = new_test_user(self.env, login='ap_dual', groups='mos_approvals.group_mos_dept_manager,mos_approvals.group_mos_finance_manager')
        self.fin.group_ids -= self.env.ref('mos_approvals.group_mos_finance_manager')  # only 'both' can do stage 2
        po = self._po(80000)
        po.action_mos_submit_approval()
        self._step(po, both).action_approve()  # stage 1
        with self.assertRaises(AccessError):
            self._step(po, both).action_approve()  # stage 2: the same person
        self.assertEqual(po.mos_approval_state, 'pending')

    def test_reject_and_return_need_a_comment_and_close_the_request(self):
        po = self._po(10000)
        po.action_mos_submit_approval()
        with self.assertRaises(UserError):
            self._step(po, self.dept1).action_reject('')
        self._step(po, self.dept1).action_return('Wrong price')
        self.assertEqual(po.mos_approval_state, 'returned')
        self.assertEqual(po.mos_approval_request_id.state, 'returned')
        po.action_mos_submit_approval()  # a returned document can go round again, as a new request
        self.assertEqual(po.mos_approval_state, 'pending')
        self._step(po, self.dept2).action_reject('Not needed')
        self.assertEqual(po.mos_approval_state, 'rejected')

    def test_history_is_immutable(self):
        po = self._po(10000)
        po.action_mos_submit_approval()
        request = po.mos_approval_request_id
        step = self._step(po, self.dept1)
        step.action_approve('fine')
        decided = request.step_ids[0]
        with self.assertRaises(UserError):
            decided.sudo().write({'comment': 'changed'})
        with self.assertRaises(UserError):
            decided.sudo().unlink()
        with self.assertRaises(UserError):
            request.sudo().unlink()
        with self.assertRaises(UserError):
            request.sudo().write({'amount': 1})

    def test_delegation_lets_a_delegate_decide_on_behalf(self):
        po = self._po(10000)
        stand_in = new_test_user(self.env, login='ap_stand_in', groups='mos_core.group_mos_user')
        self.env['mos.approval.delegation'].with_user(self.dept1).create(
            {'delegate_id': stand_in.id, 'date_to': fields.Datetime.now() + timedelta(days=3), 'doc_type': 'purchase.order', 'max_amount': 20000}
        )
        self.env['mos.approval.delegation'].with_user(self.dept2).create(
            {'delegate_id': stand_in.id, 'date_to': fields.Datetime.now() + timedelta(days=3), 'doc_type': 'purchase.order', 'max_amount': 20000}
        )
        po.action_mos_submit_approval()
        step = self._step(po, stand_in)
        self.assertTrue(step, 'the delegate sees the task')
        step.action_approve('covering')
        decided = po.mos_approval_request_id.step_ids[0]
        self.assertEqual(decided.decided_by_id, stand_in)
        self.assertIn(decided.on_behalf_of_id, self.dept1 | self.dept2)

    def test_delegation_respects_its_ceiling(self):
        stand_in = new_test_user(self.env, login='ap_ceiling', groups='mos_core.group_mos_user')
        for manager in (self.dept1, self.dept2):
            self.env['mos.approval.delegation'].with_user(manager).create(
                {'delegate_id': stand_in.id, 'date_to': fields.Datetime.now() + timedelta(days=3), 'max_amount': 5000}
            )
        po = self._po(10000)
        po.action_mos_submit_approval()
        with self.assertRaises(AccessError):
            self._step(po, stand_in).action_approve()

    def test_overdue_step_escalates_to_the_fallback_role(self):
        po = self._po(80000)
        po.action_mos_submit_approval()
        self._step(po, self.dept1).action_approve()
        step = self.env['mos.approval.step'].sudo().search([('request_id', '=', po.mos_approval_request_id.id), ('state', '=', 'pending')])
        self.assertEqual(len(step), 1)
        self.assertNotIn(self.gm, step.eligible_user_ids)
        self.env.cr.execute('UPDATE mos_approval_step SET due_at = %s WHERE id = %s', [fields.Datetime.now() - timedelta(hours=1), step.id])
        step.invalidate_recordset()
        self.env['mos.approval.step']._cron_escalate()
        step.invalidate_recordset()
        self.assertTrue(step.escalated)
        self.assertIn(self.gm, step.eligible_user_ids)
        self._step(po, self.gm).action_approve('escalated approval')
        self.assertEqual(po.mos_approval_state, 'approved')

    def test_bulk_approval_only_where_the_rule_allows_it(self):
        po = self._po(10000)
        po.action_mos_submit_approval()
        with self.assertRaises(UserError):
            self._step(po, self.dept1).action_bulk_approve()
        self.env['mos.approval.rule'].search([('sequence', '=', 10)]).write({'allow_bulk': True})
        po2 = self._po(11000)
        po2.action_mos_submit_approval()
        steps = self.env['mos.approval.step'].with_user(self.dept1).search([('state', '=', 'pending'), ('request_id.doc_name', '=', po2.display_name)])
        steps.action_bulk_approve()
        self.assertEqual(po2.mos_approval_state, 'approved')

    def test_recall_by_the_submitter_only(self):
        po = self._po(10000)
        po.action_mos_submit_approval()
        with self.assertRaises(AccessError):
            po.mos_approval_request_id.with_user(self.dept1).action_recall()
        po.mos_approval_request_id.with_user(self.buyer).action_recall()
        self.assertEqual(po.mos_approval_state, 'cancelled')
        self.assertEqual(po.mos_approval_request_id.state, 'cancelled')

    def test_submitted_order_is_locked_while_it_waits(self):
        po = self._po(10000)
        po.action_mos_submit_approval()
        with self.assertRaises(UserError):
            po.write({'partner_id': self.env['res.partner'].create({'name': 'Other'}).id})
