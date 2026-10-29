from odoo import api, fields, models
from odoo.exceptions import UserError


class MosApprovable(models.AbstractModel):
    """Give any document an approval chain. The model says what its amount and site are and what happens when the
    chain ends; the matrix decides who must approve."""

    _name = 'mos.approvable'
    _description = 'Document that can need approval'

    mos_approval_request_id = fields.Many2one('mos.approval.request', string='Approval', readonly=True, copy=False)
    mos_approval_state = fields.Selection(
        [('none', 'Not submitted'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('returned', 'Returned for changes'), ('cancelled', 'Recalled')],
        default='none',
        readonly=True,
        copy=False,
        string='Approval status',
    )
    mos_my_step_id = fields.Many2one('mos.approval.step', compute='_compute_my_step', string='Your decision', help='The step of the approval waiting for the person looking at the document, if there is one.')
    mos_approval_comment = fields.Text(compute='_compute_approval_comment', string='Approval comments', help='What the approvers wrote when they decided.')

    @api.depends('mos_approval_request_id.step_ids.comment')
    def _compute_approval_comment(self):
        for doc in self:
            steps = doc.mos_approval_request_id.sudo().step_ids.sorted('sequence')
            doc.mos_approval_comment = '\n'.join(comment for comment in steps.mapped('comment') if comment)

    @api.depends('mos_approval_request_id.step_ids.state')
    @api.depends_context('uid')
    def _compute_my_step(self):
        user = self.env.user
        for doc in self:
            steps = doc.mos_approval_request_id.sudo().step_ids.filtered(lambda s: s.state == 'pending' and s._my_authority(user)[0])
            doc.mos_my_step_id = steps[:1]

    def _mos_decide_dialog(self, outcome):
        self.ensure_one()
        step = self.mos_my_step_id
        if not step:
            raise UserError(self.env._('This document is not waiting for your decision.'))
        return {
            'type': 'ir.actions.act_window',
            'name': {'approved': self.env._('Approve'), 'rejected': self.env._('Reject'), 'returned': self.env._('Send back for changes')}[outcome],
            'res_model': 'mos.approval.decide',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_step_id': step.id, 'default_outcome': outcome},
        }

    def action_mos_decide_approve(self):
        return self._mos_decide_dialog('approved')

    def action_mos_decide_return(self):
        return self._mos_decide_dialog('returned')

    def action_mos_decide_reject(self):
        return self._mos_decide_dialog('rejected')

    # -- what a model provides ---------------------------------------------------------------------------------------
    def _mos_doc_type(self):
        return self._name

    def _mos_approval_amount(self):
        raise NotImplementedError

    def _mos_approval_site(self):
        return self.env['mos.site']

    # -- behaviour -------------------------------------------------------------------------------------------------
    def _mos_approval_required(self):
        self.ensure_one()
        return bool(self.env['mos.approval.rule'].sudo().matching(self._mos_doc_type(), self._mos_approval_amount(), self._mos_approval_site()))

    def _mos_attestation_dialog(self, method='action_mos_submit_approval'):
        """The dialog that asks the submitter to confirm the statement, or None when none is needed: the statement is off, it was
        already confirmed, or the document needs no approval. The dialog runs ``method`` again once the box is ticked."""
        if not self.env['mos.approval.request']._attestation_required() or self.env.context.get('mos_attestation'):
            return None
        docs = self.filtered(lambda d: d._mos_approval_required())
        if not docs:
            return None
        if len(self) != 1:
            raise UserError(self.env._('While submitters must confirm a statement, documents are submitted one at a time.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Submit for approval'),
            'res_model': 'mos.approval.submit',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_method': method},
        }

    def action_mos_submit_approval(self):
        dialog = self._mos_attestation_dialog()
        if dialog:
            return dialog
        for doc in self:
            if doc.mos_approval_state == 'pending':
                raise UserError(self.env._('This document is already waiting for approval.'))
            request = self.env['mos.approval.request'].submit(doc, doc._mos_doc_type(), doc._mos_approval_amount(), doc._mos_approval_site())
            doc.sudo().write({'mos_approval_request_id': request.id, 'mos_approval_state': 'pending'})

    def action_mos_recall_approval(self):
        for doc in self:
            doc.mos_approval_request_id.action_recall()

    def _mos_require_approved(self):
        """Call before an action that must not happen without approval."""
        for doc in self:
            if doc._mos_approval_required() and doc.mos_approval_state != 'approved':
                raise UserError(self.env._('%s needs approval first: submit it for approval.', doc.display_name))

    def _mos_approval_done(self, request):
        self.sudo().write({'mos_approval_state': 'approved'})

    def _mos_approval_closed(self, request, state):
        if state != 'approved':
            self.sudo().write({'mos_approval_state': state})
