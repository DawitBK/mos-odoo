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
    score = fields.Float(string='Quality score', digits=(5, 1), compute='_compute_quality', store=True)
    grade_id = fields.Many2one('mos.lab.grade', compute='_compute_quality', store=True, string='Grade')
    auto_deduction_pct = fields.Float(string='Rule-based deduction (%)', digits=(5, 2), compute='_compute_quality', store=True)
    auto_price_adjust_pct = fields.Float(string='Rule-based price adjustment (%)', digits=(5, 2), compute='_compute_quality', store=True)
    price_adjust_pct = fields.Float(string='Price adjustment (%)', digits=(5, 2), readonly=True, help='Fixed with the decision; negative lowers the price paid.')
    samples_required = fields.Integer(readonly=True, help='From the sampling plan for this product and consignment size.')
    samples_taken = fields.Integer()
    sample_ids = fields.One2many('mos.lab.sample', 'inspection_id', string='Retention samples')
    params_total = fields.Integer(string='Total parameters', compute='_compute_param_counts')
    params_passed = fields.Integer(string='Passed', compute='_compute_param_counts')
    params_failed = fields.Integer(string='Failed', compute='_compute_param_counts')

    @api.depends('result_ids.valid', 'result_ids.passed', 'result_ids.line_id', 'spec_id.line_ids')
    def _compute_param_counts(self):
        for insp in self:
            current = insp._current_results()
            insp.params_total = len(insp.spec_id.line_ids)
            insp.params_passed = sum(1 for r in current.values() if r.passed)
            insp.params_failed = sum(1 for r in current.values() if not r.passed)

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
            if 'samples_required' not in vals and vals.get('visit_id') and vals.get('product_id'):
                visit = self.env['mos.gate.visit'].browse(vals['visit_id'])
                weight = visit.net_weight or visit.ticket_id.first_weight
                product = self.env['product.product'].browse(vals['product_id'])
                vals['samples_required'] = self.env['mos.lab.sampling.plan'].samples_required(product, weight)
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

    @api.depends('result_ids.valid', 'result_ids.value', 'result_ids.line_id', 'spec_id.line_ids.weight', 'spec_id.grade_ids.min_score')
    def _compute_quality(self):
        for insp in self:
            current = insp._current_results()
            lines = insp.spec_id.line_ids
            insp.auto_deduction_pct = insp.auto_price_adjust_pct = insp.score = 0.0
            insp.grade_id = False
            if not lines or any(line not in current for line in lines):
                continue
            total = sum(lines.mapped('weight'))
            good = sum(line.weight for line, result in current.items() if result.passed)
            insp.score = round(100.0 * good / total, 1) if total else 0.0
            weight_cut = price_cut = 0.0
            for line, result in current.items():
                band = line.rule_ids.filtered(lambda r: r.from_value <= result.value <= r.to_value)[:1]
                weight_cut += band.weight_deduction_pct
                price_cut += band.price_adjust_pct
            insp.auto_deduction_pct = min(weight_cut, 99.99)
            insp.auto_price_adjust_pct = max(-100.0, min(price_cut, 100.0))
            grades = insp.spec_id.grade_ids.filtered(lambda g: g.min_score <= insp.score).sorted('min_score', reverse=True)
            insp.grade_id = grades[:1]

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
        return self.env['mos.lab.result'].create({'inspection_id': self.id, 'line_id': line.id, 'value': value})

    coa_ids = fields.One2many('mos.lab.coa', 'inspection_id', string='Certificates of analysis')

    def action_issue_coa(self):
        """A lab manager issues the certificate of analysis for an accepted, decided inspection (the screen's button for FR-QC-021)."""
        self.ensure_one()
        if not self.env.user.has_group('mos_lab.group_mos_lab_manager'):
            raise UserError(self.env._('Only a lab manager can issue a certificate of analysis.'))
        if self.coa_ids.filtered(lambda c: c.state == 'issued'):
            raise UserError(self.env._('A certificate has already been issued for this inspection; revoke it first to issue another.'))
        coa = self.env['mos.lab.coa'].issue(self, self.visit_id)
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.lab.coa', 'res_id': coa.id, 'view_mode': 'form', 'target': 'current'}

    def action_submit_results(self):
        for insp in self:
            if insp.suggested_outcome == 'incomplete':
                raise UserError(self.env._('Every parameter needs a valid result before the results are submitted.'))
            if insp.samples_taken < insp.samples_required:
                raise UserError(
                    self.env._('The sampling plan asks for %(need)s samples and %(got)s were recorded.', need=insp.samples_required, got=insp.samples_taken)
                )
            insp.write({'state': 'tested', 'tested_by_id': self.env.uid})
            if not insp.sample_ids:
                self.env['mos.lab.sample'].sudo().create({'inspection_id': insp.id})

    def decide(self, decision, deduction_pct=0.0, note=''):
        """The usage decision. The deduction from the quality rules always applies; a smaller one is a concession."""
        self.ensure_one()
        if self.state != 'tested':
            raise UserError(self.env._('Submit the results before deciding.'))
        if self.env.uid == self.tested_by_id.id:
            raise UserError(self.env._('The usage decision must be made by someone other than the person who entered the results.'))
        if decision == 'accept_deduction' and not 0 < deduction_pct < 100:
            raise UserError(self.env._('Accept with deduction needs a deduction between 0 and 100 percent.'))
        auto = self.auto_deduction_pct
        price_adjust = self.auto_price_adjust_pct
        if decision == 'reject':
            deduction_pct = price_adjust = 0.0
        else:
            if deduction_pct and deduction_pct < auto:
                if not note:
                    raise UserError(self.env._('A deduction below the rule-based %s%% needs a written justification.', auto))
                if not self.env.user.has_group('mos_lab.group_mos_lab_manager'):
                    raise UserError(self.env._('Only a lab manager can go below the rule-based deduction.'))
            deduction_pct = deduction_pct or auto
            if deduction_pct and decision == 'accept':
                decision = 'accept_deduction'
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
                'price_adjust_pct': price_adjust,
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
    unit = fields.Char(related='line_id.unit')
    range_text = fields.Char(string='Standard / acceptable range', compute='_compute_range_text')
    outcome = fields.Selection([('pass', 'Pass'), ('fail', 'Fail'), ('invalid', 'Invalidated')], string='Result', compute='_compute_outcome')

    @api.depends('line_id.min_value', 'line_id.max_value', 'line_id.unit')
    def _compute_range_text(self):
        for r in self:
            low, high, unit = r.line_id.min_value, r.line_id.max_value, r.line_id.unit or ''
            if not low:
                text = f'<= {high:g}'
            elif not high:
                text = f'>= {low:g}'
            else:
                text = f'{low:g} to {high:g}'
            r.range_text = f'{text} {unit}'.strip()

    @api.depends('valid', 'passed')
    def _compute_outcome(self):
        for r in self:
            r.outcome = 'invalid' if not r.valid else 'pass' if r.passed else 'fail'

    @api.depends('value', 'line_id.min_value', 'line_id.max_value')
    def _compute_passed(self):
        for r in self:
            r.passed = r.line_id.min_value <= r.value <= r.line_id.max_value

    @api.model_create_multi
    def create(self, vals_list):
        inspections = self.env['mos.lab.inspection'].browse([v['inspection_id'] for v in vals_list])
        lines = self.env['mos.lab.spec.line'].browse([v['line_id'] for v in vals_list])
        for insp, line in zip(inspections, lines):
            if insp.state == 'decided':
                raise UserError(self.env._('The inspection is decided; results can no longer be entered.'))
            if line.spec_id != insp.spec_id:
                raise UserError(self.env._("That parameter does not belong to this inspection's specification."))
        for insp in inspections.filtered(lambda i: i.state == 'tested'):
            insp.write({'state': 'open'})
        return super().create(vals_list)

    def action_open_invalidate(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Invalidate result'),
            'res_model': 'mos.lab.invalidate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_result_id': self.id},
        }

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
