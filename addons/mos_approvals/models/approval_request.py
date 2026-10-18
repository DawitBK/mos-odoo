from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

FINAL = ('approved', 'rejected', 'returned', 'cancelled')


class MosApprovalRequest(models.Model):
    """A document waiting for approval, with its chain of steps.

    The history is immutable: a decided step, a closed request and every comment can never be changed or deleted,
    not even for a rejected or cancelled document (FR-PLT-054). Segregation of duties is enforced in
    ``_eligible_users`` (FR-PLT-028, 029).
    """

    _name = 'mos.approval.request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'MOS approval request'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Many2oneReference(model_field='res_model', required=True, readonly=True)
    doc_name = fields.Char(readonly=True)
    amount = fields.Float(readonly=True)
    site_id = fields.Many2one('mos.site', readonly=True)
    requester_id = fields.Many2one('res.users', readonly=True, default=lambda s: s.env.uid)
    state = fields.Selection(
        [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('returned', 'Returned for changes'), ('cancelled', 'Recalled')],
        default='pending',
        readonly=True,
        tracking=True,
    )
    step_ids = fields.One2many('mos.approval.step', 'request_id', string='Steps')
    current_step_id = fields.Many2one('mos.approval.step', compute='_compute_current')
    submitted_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    closed_at = fields.Datetime(readonly=True)

    @api.depends('step_ids.state')
    def _compute_current(self):
        for req in self:
            req.current_step_id = req.step_ids.filtered(lambda s: s.state == 'pending')[:1]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.approval.request')
        return super().create(vals_list)

    def write(self, vals):
        if any(r.state in FINAL for r in self) and set(vals) - {'message_follower_ids', 'message_ids'}:
            raise UserError(self.env._('The approval history is final and cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(self.env._('Approval history is never deleted.'))

    # -- building a request ------------------------------------------------------------------------------------------
    @api.model
    def submit(self, document, doc_type, amount, site=None):
        """Create the request and its steps from the matrix, or refuse when nobody could approve a step."""
        rules = self.env['mos.approval.rule'].matching(doc_type, amount, site)
        if not rules:
            raise UserError(self.env._('No approval rule applies to this document, so it needs no approval.'))
        requester = self.env.user
        request = self.sudo().create(
            {
                'res_model': document._name,
                'res_id': document.id,
                'doc_name': document.display_name,
                'amount': amount,
                'site_id': site.id if site else False,
                'requester_id': requester.id,
            }
        )
        steps = self.env['mos.approval.step'].sudo()
        for number, rule in enumerate(rules, start=1):
            steps |= steps.with_context(mos_approval_internal=True).create(
                {
                    'request_id': request.id,
                    'sequence': number,
                    'group_id': rule.group_id.id,
                    'fallback_group_id': rule.fallback_group_id.id,
                    'sla_hours': rule.sla_hours,
                    'allow_bulk': rule.allow_bulk,
                }
            )
        # FR-PLT-029: every step must have somebody who may decide it, after segregation of duties.
        for step in steps:
            if not step._eligible_users(request, steps):
                request.unlink_force()
                raise UserError(
                    self.env._(
                        'Stage %(n)s (%(role)s) has nobody who may approve this document after segregation of duties. Ask an administrator to assign the role.',
                        n=step.sequence,
                        role=step.group_id.display_name,
                    )
                )
        steps[:1].with_context(mos_approval_internal=True)._activate()
        self.env['mos.audit.event'].record('approval_submitted', request, f'{request.name}: {request.doc_name}, {len(steps)} stages')
        return request

    def unlink_force(self):
        """Only for a request that never took effect (a refused submission)."""
        self.env.cr.execute('DELETE FROM mos_approval_step WHERE request_id = ANY(%s)', [self.ids])
        self.env.cr.execute('DELETE FROM mos_approval_request WHERE id = ANY(%s)', [self.ids])
        self.invalidate_recordset()

    # -- deciding ----------------------------------------------------------------------------------------------------
    def _document(self):
        self.ensure_one()
        return self.env[self.res_model].sudo().browse(self.res_id)

    def action_recall(self):
        """The submitter takes the document back; it returns to draft and the request closes (FR-PLT-025)."""
        for req in self:
            if req.state != 'pending':
                raise UserError(self.env._('Only a pending request can be recalled.'))
            if req.requester_id != self.env.user and not self.env.user.has_group('mos_core.group_mos_admin'):
                raise AccessError(self.env._('Only the person who submitted the document can recall it.'))
            req._close('cancelled')

    def _close(self, state):
        for req in self:
            req.sudo().write({'state': state, 'closed_at': fields.Datetime.now()})
            req.sudo().step_ids.filtered(lambda s: s.state in ('waiting', 'pending')).with_context(mos_approval_internal=True).write({'state': 'skipped'})
            req._document()._mos_approval_closed(req, state)
            self.env['mos.audit.event'].record(f'approval_{state}', req, req.name)


class MosApprovalStep(models.Model):
    _name = 'mos.approval.step'
    _description = 'MOS approval step'
    _order = 'request_id, sequence'

    request_id = fields.Many2one('mos.approval.request', required=True, ondelete='restrict', index=True)
    sequence = fields.Integer(required=True)
    group_id = fields.Many2one('res.groups', string='Approving role', required=True)
    fallback_group_id = fields.Many2one('res.groups')
    sla_hours = fields.Integer(default=24)
    allow_bulk = fields.Boolean()
    state = fields.Selection(
        [('waiting', 'Waiting'), ('pending', 'Needs a decision'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('returned', 'Returned'), ('skipped', 'Not needed')],
        default='waiting',
        required=True,
    )
    eligible_user_ids = fields.Many2many('res.users', 'mos_approval_step_eligible_rel', 'step_id', 'user_id', string='May decide')
    decided_by_id = fields.Many2one('res.users', readonly=True)
    on_behalf_of_id = fields.Many2one('res.users', string='On behalf of', readonly=True)
    decided_at = fields.Datetime(readonly=True)
    comment = fields.Text(readonly=True)
    due_at = fields.Datetime(readonly=True)
    escalated = fields.Boolean(readonly=True)

    # Display of the request in the inbox.
    doc_name = fields.Char(related='request_id.doc_name')
    amount = fields.Float(related='request_id.amount')
    requester_id = fields.Many2one(related='request_id.requester_id')
    request_state = fields.Selection(related='request_id.state')
    res_model = fields.Char(related='request_id.res_model')
    res_id = fields.Many2oneReference(related='request_id.res_id', model_field='res_model')

    def write(self, vals):
        if not self.env.context.get('mos_approval_internal'):
            raise UserError(self.env._('Approval steps change only through Approve, Reject or Return.'))
        locked = self.filtered(lambda s: s.state in ('approved', 'rejected', 'returned'))
        if locked and set(vals) - {'eligible_user_ids'}:
            raise UserError(self.env._('A decided step is part of the history and cannot be changed.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(self.env._('Approval history is never deleted.'))

    # -- who may decide ----------------------------------------------------------------------------------------------
    def _eligible_users(self, request=None, siblings=None):
        """Members of the approving role, minus the people segregation of duties excludes: whoever submitted or created
        the document, and anyone who already approved an earlier stage of this same request."""
        self.ensure_one()
        request = request or self.request_id
        siblings = siblings if siblings is not None else request.step_ids
        users = self.env['res.users'].sudo().search([('all_group_ids', 'in', self.group_id.ids), ('share', '=', False)])
        document = request._document()
        excluded = request.requester_id | (document.create_uid if document else self.env['res.users'])
        excluded |= siblings.filtered(lambda s: s.state == 'approved').mapped('decided_by_id')
        if document and hasattr(document, '_mos_approval_excluded_users'):
            excluded |= document._mos_approval_excluded_users()  # e.g. the analyst who entered the results being conceded
        return users - excluded

    def _activate(self):
        self.ensure_one()
        eligible = self._eligible_users()
        delegates = self.env['mos.approval.delegation'].sudo().delegates_of(eligible, self.request_id)
        self.write(
            {
                'state': 'pending',
                'eligible_user_ids': [(6, 0, (eligible | delegates).ids)],
                'due_at': fields.Datetime.now() + timedelta(hours=self.sla_hours or 24),
            }
        )
        for user in (eligible | delegates):
            self.request_id.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=user.id,
                summary=self.env._('Approve %s', self.request_id.doc_name),
                note=self.env._('%(doc)s, amount %(amount)s, needs a decision (%(n)s).', doc=self.request_id.doc_name, amount=self.request_id.amount, n=self.request_id.name),
            )

    def _my_authority(self, user):
        """(allowed, on_behalf_of): whether this user may decide this step, directly or through a delegation."""
        self.ensure_one()
        if self.state != 'pending':
            return False, False
        eligible = self._eligible_users()
        if self.escalated:
            # After escalation the fallback role may decide too (it was added to eligible_user_ids), still minus segregation of duties.
            earlier = self.request_id.step_ids.filtered(lambda s: s.state == 'approved').mapped('decided_by_id')
            eligible |= self.eligible_user_ids - self.request_id.requester_id - earlier
        if user in eligible:
            return True, False
        for delegator in self.env['mos.approval.delegation'].sudo().delegators_of(user, self.request_id) & eligible:
            return True, delegator
        return False, False

    # -- decisions -----------------------------------------------------------------------------------------------------
    def _decide(self, outcome, comment):
        self.ensure_one()
        user = self.env.user
        allowed, on_behalf = self._my_authority(user)
        if not allowed:
            raise AccessError(self.env._('You may not decide this step: it is not yours, or segregation of duties excludes you.'))
        if outcome in ('rejected', 'returned') and not (comment or '').strip():
            raise UserError(self.env._('A comment is required to reject a document or send it back.'))
        step = self.sudo().with_context(mos_approval_internal=True)
        step.write(
            {
                'state': outcome,
                'decided_by_id': user.id,
                'on_behalf_of_id': on_behalf.id if on_behalf else False,
                'decided_at': fields.Datetime.now(),
                'comment': comment or False,
            }
        )
        request = self.request_id.sudo()
        request.message_post(body=self.env._('Stage %(n)s %(outcome)s by %(who)s%(behalf)s. %(comment)s', n=self.sequence, outcome=outcome, who=user.name, behalf=self.env._(' on behalf of %s', on_behalf.name) if on_behalf else '', comment=comment or ''))
        request.activity_ids.filtered(lambda a: a.res_id == request.id).action_feedback()
        self.env['mos.audit.event'].record(f'approval_step_{outcome}', request, f'{request.name} stage {self.sequence} by {user.login}')
        if outcome == 'approved':
            nxt = request.step_ids.filtered(lambda s: s.state == 'waiting')[:1]
            if nxt:
                nxt.sudo().with_context(mos_approval_internal=True)._activate()
            else:
                request._close('approved')
                request._document()._mos_approval_done(request)
        else:
            request._close(outcome)

    def action_approve(self, comment=''):
        for step in self:
            step._decide('approved', comment)

    def action_reject(self, comment=''):
        for step in self:
            step._decide('rejected', comment)

    def action_return(self, comment=''):
        for step in self:
            step._decide('returned', comment)

    def action_bulk_approve(self):
        """Approve the selected steps; each is still checked on its own, and a rule may forbid bulk (FR-PLT-032)."""
        done = 0
        for step in self:
            if not step.allow_bulk:
                raise UserError(self.env._('Bulk approval is off for %s: open it and decide on its own.', step.doc_name))
            step._decide('approved', self.env._('Bulk approval'))
            done += 1
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'message': self.env._('%s approved', done), 'type': 'success'}}

    # -- escalation ---------------------------------------------------------------------------------------------------
    @api.model
    def _cron_escalate(self):
        """A step past its deadline reminds its approvers, then reaches the fallback role (FR-PLT-027)."""
        late = self.sudo().search([('state', '=', 'pending'), ('due_at', '<', fields.Datetime.now()), ('escalated', '=', False)])
        for step in late.with_context(mos_approval_internal=True):
            extra = self.env['res.users']
            if step.fallback_group_id:
                extra = self.env['res.users'].sudo().search([('all_group_ids', 'in', step.fallback_group_id.ids), ('share', '=', False)]) - step.request_id.requester_id
            step.write({'escalated': True, 'eligible_user_ids': [(4, u.id) for u in extra]})
            step.request_id.message_post(body=self.env._('Stage %s is overdue and has been escalated.', step.sequence))
            for user in extra | step.eligible_user_ids:
                step.request_id.activity_schedule('mail.mail_activity_data_todo', user_id=user.id, summary=self.env._('Overdue approval: %s', step.doc_name))
