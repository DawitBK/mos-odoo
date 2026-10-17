from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MosApprovalDelegation(models.Model):
    """A person hands their approvals to someone else for a period, optionally for one kind of document and up to a
    ceiling. While it runs the delegate sees and decides the task 'on behalf of' the delegator (FR-PLT-026)."""

    _name = 'mos.approval.delegation'
    _inherit = ['mos.audited']
    _description = 'MOS approval delegation'
    _order = 'date_from desc'

    delegator_id = fields.Many2one('res.users', required=True, default=lambda s: s.env.user)
    delegate_id = fields.Many2one('res.users', required=True)
    date_from = fields.Datetime(required=True, default=fields.Datetime.now)
    date_to = fields.Datetime(required=True)
    doc_type = fields.Selection([('purchase.order', 'Purchase order')], string='Only for')
    max_amount = fields.Float(help='Zero means no ceiling.')
    active = fields.Boolean(default=True)

    @api.constrains('delegator_id', 'delegate_id', 'date_from', 'date_to')
    def _check(self):
        for d in self:
            if d.delegator_id == d.delegate_id:
                raise ValidationError(self.env._('Nobody can delegate to themselves.'))
            if d.date_to <= d.date_from:
                raise ValidationError(self.env._('The delegation must end after it starts.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._refresh_open_steps()
        return records

    def _applies(self, request):
        self.ensure_one()
        now = fields.Datetime.now()
        return (
            self.active
            and self.date_from <= now <= self.date_to
            and (not self.doc_type or self.doc_type == request.res_model)
            and (not self.max_amount or request.amount <= self.max_amount)
        )

    @api.model
    def delegates_of(self, users, request):
        """The delegates who may stand in for any of these users on this request."""
        found = self.env['res.users']
        for d in self.search([('delegator_id', 'in', users.ids)]):
            if d._applies(request):
                found |= d.delegate_id
        return found - request.requester_id

    @api.model
    def delegators_of(self, user, request):
        found = self.env['res.users']
        for d in self.search([('delegate_id', '=', user.id)]):
            if d._applies(request):
                found |= d.delegator_id
        return found

    def _refresh_open_steps(self):
        """A delegation made after a request was raised still reaches the open steps."""
        steps = self.env['mos.approval.step'].sudo().search([('state', '=', 'pending')])
        for step in steps.with_context(mos_approval_internal=True):
            eligible = step._eligible_users()
            step.write({'eligible_user_ids': [(6, 0, (eligible | self.delegates_of(eligible, step.request_id) | (step.eligible_user_ids & eligible)).ids)]})
