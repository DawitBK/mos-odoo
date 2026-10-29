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
    mos_approval_comment = fields.Text(compute='_compute_approval_comment', string='Approval comments', help='What the approvers wrote when they decided.')

    @api.depends('mos_approval_request_id.step_ids.comment')
    def _compute_approval_comment(self):
        for doc in self:
            steps = doc.mos_approval_request_id.sudo().step_ids.sorted('sequence')
            doc.mos_approval_comment = '\n'.join(comment for comment in steps.mapped('comment') if comment)

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
