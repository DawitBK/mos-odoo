from odoo import fields, models


class MosApprovalDecide(models.TransientModel):
    """The small dialog where the approver types the comment that goes with the decision."""

    _name = 'mos.approval.decide'
    _description = 'Decide an approval step'

    step_id = fields.Many2one('mos.approval.step', required=True, readonly=True)
    outcome = fields.Selection([('approved', 'Approve'), ('rejected', 'Reject'), ('returned', 'Send back for changes')], required=True, readonly=True)
    comment = fields.Text(help='Required to reject or send back.')

    def action_confirm(self):
        self.ensure_one()
        step = self.step_id
        {'approved': step.action_approve, 'rejected': step.action_reject, 'returned': step.action_return}[self.outcome](self.comment or '')
        return {'type': 'ir.actions.act_window_close'}
