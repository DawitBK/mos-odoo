from odoo import api, fields, models
from odoo.exceptions import UserError


class MosLabInspection(models.Model):
    """An inspection of one delivery against the specification in force when it was opened.

    Results are never overwritten: a wrong result is invalidated with a cause and entered again
    (FR-QC-008, 010). The evaluation is automatic (FR-QC-009). The usage decision is made by a
    different person than the one who entered the results (segregation of duties), is final, and
    carries the quality deduction that fixes the payable quantity (FR-QC-012..014).
    """

    _name = 'mos.lab.inspection'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS inspection'
    _order = 'id desc'
    _mos_final_states = ('decided',)
    _mos_editable_after_post = set()

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one(related='visit_id.site_id', store=True)
    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='restrict', index=True)
    product_id = fields.Many2one('product.product', required=True)
    spec_id = fields.Many2one('mos.lab.spec', required=True, readonly=True, string='Specification (frozen)')
    state = fields.Selection([('open', 'Open'), ('tested', 'Tested'), ('decided', 'Decided')], default='open', required=True, readonly=True, tracking=True)
    result_ids = fields.One2many('mos.lab.result', 'inspection_id', string='Results')
    tested_by_id = fields.Many2one('res.users', readonly=True)
    suggested_outcome = fields.Selection(
        [('incomplete', 'Incomplete'), ('pass', 'Pass'), ('fail', 'Fail')], compute='_compute_suggested', store=True
    )
    decision = fields.Selection(
        [('accept', 'Accept'), ('accept_deduction', 'Accept with deduction'), ('reject', 'Reject')], readonly=True, tracking=True
    )
    deduction_pct = fields.Float(string='Deduction (%)', readonly=True)
    decision_note = fields.Text(readonly=True)
    decided_by_id = fields.Many2one('res.users', readonly=True)
    net_weight = fields.Float(related='visit_id.net_weight', string='Net weight (kg)')
    payable_qty = fields.Float(string='Payable quantity (kg)', digits=(12, 1), compute='_compute_payable', store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.lab.inspection')
            if not vals.get('spec_id'):
                spec = self.env['mos.lab.spec'].in_force(self.env['product.product'].browse(vals['product_id']))
                if not spec:
                    raise UserError(self.env._('No approved specification exists for this product.'))
                vals['spec_id'] = spec.id
        return super().create(vals_list)

    @api.depends('result_ids.valid', 'result_ids.passed', 'result_ids.line_id', 'spec_id.line_ids')
    def _compute_suggested(self):
        for insp in self:
            current = insp._current_results()
            lines = insp.spec_id.line_ids
            if not lines or any(line not in current for line in lines):
                insp.suggested_outcome = 'incomplete'
            else:
                insp.suggested_outcome = 'pass' if all(r.passed for r in current.values()) else 'fail'

    @api.depends('net_weight', 'deduction_pct', 'decision')
    def _compute_payable(self):
        for insp in self:
            if insp.decision == 'reject':
                insp.payable_qty = 0.0
            else:
                insp.payable_qty = round(insp.net_weight * (1 - insp.deduction_pct / 100.0), 1)

    def _current_results(self):
        """Latest valid result per specification parameter."""
        self.ensure_one()
        latest = {}
        for r in self.result_ids.filtered('valid').sorted('id'):
            latest[r.line_id] = r
        return latest

    def record_result(self, line, value):
        self.ensure_one()
        if self.state == 'decided':
            raise UserError(self.env._('The inspection is decided; results can no longer be entered.'))
        if line.spec_id != self.spec_id:
            raise UserError(self.env._('That parameter does not belong to this inspection\'s specification.'))
        if self.state == 'tested':
            self.write({'state': 'open'})
        return self.env['mos.lab.result'].create({'inspection_id': self.id, 'line_id': line.id, 'value': value})

    def action_submit_results(self):
        for insp in self:
            if insp.suggested_outcome == 'incomplete':
                raise UserError(self.env._('Every parameter needs a valid result before the results are submitted.'))
            insp.write({'state': 'tested', 'tested_by_id': self.env.uid})

    def decide(self, decision, deduction_pct=0.0, note=''):
        self.ensure_one()
        if self.state != 'tested':
            raise UserError(self.env._('Submit the results before deciding.'))
        if self.env.uid == self.tested_by_id.id:
            raise UserError(self.env._('The usage decision must be made by someone other than the person who entered the results.'))
        if decision == 'accept_deduction' and not 0 < deduction_pct < 100:
            raise UserError(self.env._('Accept with deduction needs a deduction between 0 and 100 percent.'))
        if decision != 'accept_deduction':
            deduction_pct = 0.0
        if decision == 'accept' and self.suggested_outcome == 'fail':
            if not note:
                raise UserError(self.env._('Accepting a failed result is a concession and needs a written justification.'))
            if not self.env.user.has_group('mos_lab.group_mos_lab_manager'):
                raise UserError(self.env._('Only a lab manager can accept a failed result under concession.'))
        self.write(
            {
                'state': 'decided',
                'decision': decision,
                'deduction_pct': deduction_pct,
                'decision_note': note,
                'decided_by_id': self.env.uid,
            }
        )
        self.env['mos.audit.event'].record('inspection_decided', self, f'{self.name}: {decision}, deduction {deduction_pct}%')


class MosLabResult(models.Model):
    _name = 'mos.lab.result'
    _description = 'MOS inspection result'
    _order = 'id'

    inspection_id = fields.Many2one('mos.lab.inspection', required=True, ondelete='restrict', index=True)
    line_id = fields.Many2one('mos.lab.spec.line', required=True, ondelete='restrict', string='Parameter')
    value = fields.Float(digits=(12, 3), required=True)
    entered_by_id = fields.Many2one('res.users', default=lambda s: s.env.uid, readonly=True)
    valid = fields.Boolean(default=True)
    invalid_cause = fields.Char()
    passed = fields.Boolean(compute='_compute_passed', store=True)

    @api.depends('value', 'line_id.min_value', 'line_id.max_value')
    def _compute_passed(self):
        for r in self:
            r.passed = r.line_id.min_value <= r.value <= r.line_id.max_value

    def write(self, vals):
        locked = {'value', 'line_id', 'inspection_id'}
        if locked & set(vals):
            raise UserError(self.env._('A result is never overwritten: invalidate it and enter it again.'))
        if any(r.inspection_id.state == 'decided' for r in self):
            raise UserError(self.env._('The inspection is decided; results are final.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(self.env._('A result is never deleted: invalidate it with a cause.'))

    def action_invalidate(self, cause):
        if not cause:
            raise UserError(self.env._('A cause is required to invalidate a result.'))
        self.write({'valid': False, 'invalid_cause': cause})
