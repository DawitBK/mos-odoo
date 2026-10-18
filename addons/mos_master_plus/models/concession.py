from odoo import api, fields, models
from odoo.exceptions import UserError

TYPES = [('mos.concession', 'Quality concession (minor)'), ('mos.concession.major', 'Quality concession (major)')]


class MosApprovalRule(models.Model):
    _inherit = 'mos.approval.rule'

    doc_type = fields.Selection(selection_add=TYPES, ondelete={t[0]: 'cascade' for t in TYPES})


class MosApprovalDelegation(models.Model):
    _inherit = 'mos.approval.delegation'

    doc_type = fields.Selection(selection_add=TYPES, ondelete={t[0]: 'set null' for t in TYPES})


class MosConcession(models.Model):
    """A request to accept a delivery that failed the specification, or to take a smaller deduction than the rules give. It goes
    through the approval matrix by severity and by the value being given away; the analyst who entered the results never receives the
    approval task, and nothing changes in the inspection until the chain approves (FR-QC-011).

    Off until `mos_master_plus.concession_approval` is set; until then a lab manager decides alone, with a written justification."""

    _name = 'mos.concession'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS quality concession'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state'}

    name = fields.Char(readonly=True, copy=False, default='New')
    inspection_id = fields.Many2one('mos.lab.inspection', required=True, ondelete='restrict', index=True)
    visit_id = fields.Many2one(related='inspection_id.visit_id', store=True)
    site_id = fields.Many2one(related='inspection_id.site_id', store=True)
    product_id = fields.Many2one(related='inspection_id.product_id', store=True)
    decision = fields.Selection([('accept', 'Accept as it is'), ('accept_deduction', 'Accept with a deduction')], required=True)
    deduction_pct = fields.Float(string='Deduction asked (%)', digits=(5, 2))
    auto_deduction_pct = fields.Float(string='Deduction by the rules (%)', digits=(5, 2), readonly=True)
    failed_params = fields.Integer(readonly=True)
    justification = fields.Text(required=True)
    amount = fields.Float(string='Value given away', digits=(14, 2), readonly=True, help='What the supplier is not charged compared with applying the rules.')
    severity = fields.Selection([('minor', 'Minor'), ('major', 'Major')], readonly=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('pending', 'Waiting for approval'), ('applied', 'Applied'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.concession')
            insp = self.env['mos.lab.inspection'].browse(vals['inspection_id'])
            auto = insp.auto_deduction_pct
            failed = len(insp.result_ids.filtered(lambda r: r.valid and not r.passed))
            order_line = insp.visit_id.sudo().purchase_id.order_line.filtered(lambda l: l.product_id == insp.product_id)[:1]
            waived_pct = max(auto - (vals.get('deduction_pct') or 0.0), 0.0)
            vals.update(
                {
                    'auto_deduction_pct': auto,
                    'failed_params': failed,
                    'amount': round((insp.net_weight or 0.0) * (order_line.price_unit or insp.product_id.standard_price) * waived_pct / 100.0, 2),
                    'severity': 'major' if failed >= 2 or waived_pct >= 5.0 else 'minor',
                }
            )
        return super().create(vals_list)

    # -- approval -------------------------------------------------------------------------------------------------------------
    def _mos_doc_type(self):
        return 'mos.concession.major' if self.severity == 'major' else 'mos.concession'

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.amount

    def _mos_approval_site(self):
        return self.site_id

    def _mos_approval_excluded_users(self):
        """The analyst who entered the results does not receive the approval task."""
        return self.inspection_id.tested_by_id

    def _mos_approval_done(self, request):
        self.sudo().write({'mos_approval_state': 'approved'})
        self._apply()

    def _mos_approval_closed(self, request, state):
        if state != 'approved':
            self.sudo().write({'mos_approval_state': state, 'state': 'draft' if state in ('returned', 'cancelled') else 'rejected'})
            if state not in ('returned', 'cancelled'):
                self.message_post(body=self.env._('The concession was refused. The delivery stays undecided: reject it, or ask again with more justification.'))

    def _apply(self):
        self.ensure_one()
        insp = self.inspection_id.with_user(self.requested_by_id).with_context(mos_concession_approved=True)
        insp.decide(self.decision, self.deduction_pct, self.justification)
        self.sudo().write({'state': 'applied'})
        self.env['mos.audit.event'].record('concession_applied', self, f'{self.name}: {self.inspection_id.name} {self.decision} {self.deduction_pct}%')

    def action_submit(self):
        for concession in self:
            if concession.state != 'draft':
                raise UserError(self.env._('Only a draft concession can be submitted.'))
            if concession._mos_approval_required():
                concession.action_mos_submit_approval()
                concession.write({'state': 'pending'})
            else:
                concession.write({'mos_approval_state': 'approved'})
                concession._apply()

    def action_cancel(self):
        for concession in self:
            if concession.state not in ('draft', 'pending'):
                raise UserError(self.env._('This concession is closed.'))
            if concession.state == 'pending':
                concession.action_mos_recall_approval()
            concession.write({'state': 'cancelled'})


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    concession_ids = fields.One2many('mos.concession', 'inspection_id')

    def decide(self, decision, deduction_pct=0.0, note=''):
        self.ensure_one()
        enforce = self.env['ir.config_parameter'].sudo().get_bool('mos_master_plus.concession_approval', False)
        if self.env.context.get('mos_concession_approved') or not enforce or decision == 'reject' or self.state != 'tested':
            return super().decide(decision, deduction_pct, note)
        below_rules = bool(deduction_pct) and deduction_pct < self.auto_deduction_pct - 0.0001
        failed_as_is = decision == 'accept' and self.suggested_outcome == 'fail'
        if not (below_rules or failed_as_is):
            return super().decide(decision, deduction_pct, note)
        if self.env.uid == self.tested_by_id.id:
            raise UserError(self.env._('The usage decision must be made by someone other than the person who entered the results.'))
        if not note:
            raise UserError(self.env._('A concession needs a written justification.'))
        if not self.env.user.has_group('mos_lab.group_mos_lab_manager'):
            raise UserError(self.env._('Only a lab manager can ask for a concession.'))
        if self.concession_ids.filtered(lambda c: c.state in ('draft', 'pending')):
            raise UserError(self.env._('A concession for this inspection is already waiting.'))
        concession = self.env['mos.concession'].create({'inspection_id': self.id, 'decision': decision, 'deduction_pct': deduction_pct, 'justification': note})
        concession.action_submit()
        return concession
