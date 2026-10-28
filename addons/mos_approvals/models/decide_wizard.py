from odoo import api, fields, models
from odoo.exceptions import UserError


class MosApprovalDecide(models.TransientModel):
    """The small dialog where the approver types the comment that goes with the decision."""

    _name = 'mos.approval.decide'
    _description = 'Decide an approval step'

    step_id = fields.Many2one('mos.approval.step', required=True, readonly=True)
    outcome = fields.Selection([('approved', 'Approve'), ('rejected', 'Reject'), ('returned', 'Send back for changes')], required=True, readonly=True)
    comment = fields.Text(help='Required to reject or send back.')
    attestation_required = fields.Boolean(compute='_compute_attestation')
    attestation_text = fields.Char(compute='_compute_attestation')
    agree = fields.Boolean(string='I agree')

    @api.depends('outcome')
    def _compute_attestation(self):
        request = self.env['mos.approval.request']
        for wiz in self:
            wiz.attestation_required = wiz.outcome == 'approved' and request._attestation_required()
            wiz.attestation_text = request._attestation_text('approve') if wiz.attestation_required else False

    def action_confirm(self):
        self.ensure_one()
        step = self.step_id
        if self.attestation_required:
            if not self.agree:
                raise UserError(self.env._('Tick "I agree" to approve.'))
            step = step.with_context(mos_attestation=self.attestation_text)
        {'approved': step.action_approve, 'rejected': step.action_reject, 'returned': step.action_return}[self.outcome](self.comment or '')
        return {'type': 'ir.actions.act_window_close'}


class MosApprovalSubmit(models.TransientModel):
    """The dialog where the person sending a document for approval confirms the statement shown with "I agree"."""

    _name = 'mos.approval.submit'
    _description = 'Submit a document for approval'

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    method = fields.Char(default='action_mos_submit_approval', readonly=True, help='The action the person started; it runs again once the statement is confirmed.')
    document = fields.Char(compute='_compute_document')
    attestation_text = fields.Char(compute='_compute_document')
    agree = fields.Boolean(string='I agree')

    @api.depends('res_model', 'res_id')
    def _compute_document(self):
        for wiz in self:
            wiz.document = wiz.res_model and wiz.env[wiz.res_model].browse(wiz.res_id).display_name
            wiz.attestation_text = self.env['mos.approval.request']._attestation_text('submit')

    def action_confirm(self):
        self.ensure_one()
        if not self.agree:
            raise UserError(self.env._('Tick "I agree" to submit.'))
        document = self.env[self.res_model].browse(self.res_id).with_context(mos_attestation=self.attestation_text)
        result = getattr(document, self.method)()
        if isinstance(result, dict) and result.get('type') != 'ir.actions.act_window_close' and result.get('res_model') != 'mos.approval.submit':
            return result
        return {'type': 'ir.actions.act_window_close'}
