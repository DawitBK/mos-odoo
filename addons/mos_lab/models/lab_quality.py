from datetime import timedelta

from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError


class MosLabDeductionRule(models.Model):
    """A quality band on one specification parameter: when the measured value falls inside the band the
    delivery loses weight and/or price (FR-QC-015, 016). The rules belong to the specification version, so
    an approved version's rules never change."""

    _name = 'mos.lab.deduction.rule'
    _description = 'MOS quality deduction band'
    _order = 'line_id, from_value'

    line_id = fields.Many2one('mos.lab.spec.line', required=True, ondelete='cascade', string='Parameter')
    spec_id = fields.Many2one(related='line_id.spec_id', store=True)
    from_value = fields.Float(digits=(12, 3), required=True)
    to_value = fields.Float(digits=(12, 3), required=True)
    weight_deduction_pct = fields.Float(string='Weight deduction (%)', help='Taken off the payable quantity.')
    price_adjust_pct = fields.Float(string='Price adjustment (%)', help='Negative lowers the price paid.')

    @api.constrains('from_value', 'to_value', 'weight_deduction_pct', 'price_adjust_pct', 'line_id')
    def _check_band(self):
        for rule in self:
            if rule.to_value < rule.from_value:
                raise ValidationError(self.env._('A band must end at or above where it starts.'))
            if not 0 <= rule.weight_deduction_pct < 100:
                raise ValidationError(self.env._('A weight deduction is between 0 and 100 percent.'))
            if not -100 <= rule.price_adjust_pct <= 100:
                raise ValidationError(self.env._('A price adjustment is between -100 and 100 percent.'))
            for other in rule.line_id.rule_ids - rule:
                if rule.from_value <= other.to_value and other.from_value <= rule.to_value:
                    raise ValidationError(self.env._('Bands on %s overlap.', rule.line_id.name))

    def _guard_parent(self):
        if any(rule.line_id.spec_id.state != 'draft' for rule in self):
            raise UserError(self.env._('Rules of an approved specification cannot change; create a new version.'))

    @api.model_create_multi
    def create(self, vals_list):
        lines = self.env['mos.lab.spec.line'].browse([v['line_id'] for v in vals_list if v.get('line_id')])
        if any(line.spec_id.state != 'draft' for line in lines):
            raise UserError(self.env._('Rules of an approved specification cannot change; create a new version.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard_parent()
        return super().write(vals)

    def unlink(self):
        self._guard_parent()
        return super().unlink()


class MosLabGrade(models.Model):
    """A grade a delivery earns from its quality score, best first (FR-QC-017)."""

    _name = 'mos.lab.grade'
    _description = 'MOS quality grade'
    _order = 'spec_id, min_score desc'

    spec_id = fields.Many2one('mos.lab.spec', required=True, ondelete='cascade')
    name = fields.Char(string='Grade', required=True)
    min_score = fields.Float(string='Minimum score', digits=(5, 1), required=True)

    _grade_uniq = models.Constraint('unique(spec_id, name)', 'A grade name must be unique within a specification.')

    @api.constrains('min_score')
    def _check_score(self):
        for grade in self:
            if not 0 <= grade.min_score <= 100:
                raise ValidationError(self.env._('A score is between 0 and 100.'))

    def _guard_parent(self):
        if any(grade.spec_id.state != 'draft' for grade in self):
            raise UserError(self.env._('Grades of an approved specification cannot change; create a new version.'))

    @api.model_create_multi
    def create(self, vals_list):
        specs = self.env['mos.lab.spec'].browse([v['spec_id'] for v in vals_list if v.get('spec_id')])
        if any(spec.state != 'draft' for spec in specs):
            raise UserError(self.env._('Grades of an approved specification cannot change; create a new version.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard_parent()
        return super().write(vals)

    def unlink(self):
        self._guard_parent()
        return super().unlink()


class MosLabSamplingPlan(models.Model):
    """How many samples to draw from a consignment of a given size (FR-QC-005)."""

    _name = 'mos.lab.sampling.plan'
    _inherit = ['mos.audited']
    _description = 'MOS sampling plan'
    _rec_name = 'product_id'

    product_id = fields.Many2one('product.product', required=True, ondelete='restrict')
    method = fields.Text(help='How to draw the sample: probe points, depth, mixing.')
    line_ids = fields.One2many('mos.lab.sampling.plan.line', 'plan_id', string='Bands')
    active = fields.Boolean(default=True)

    _product_uniq = models.Constraint('unique(product_id)', 'This product already has a sampling plan.')

    @api.model
    def samples_required(self, product, weight_kg):
        plan = self.sudo().search([('product_id', '=', product.id)], limit=1)
        if not plan:
            return 0
        bands = plan.line_ids.filtered(lambda b: b.from_kg <= weight_kg).sorted('from_kg')
        return bands[-1].samples if bands else (plan.line_ids.sorted('from_kg')[:1].samples or 0)


class MosLabSamplingPlanLine(models.Model):
    _name = 'mos.lab.sampling.plan.line'
    _description = 'MOS sampling plan band'
    _order = 'from_kg'

    plan_id = fields.Many2one('mos.lab.sampling.plan', required=True, ondelete='cascade')
    from_kg = fields.Float(string='From (kg)', digits=(12, 1), required=True)
    samples = fields.Integer(required=True, default=1)

    @api.constrains('samples')
    def _check_samples(self):
        for line in self:
            if line.samples < 1:
                raise ValidationError(self.env._('Draw at least one sample.'))


class MosLabSample(models.Model):
    """A retention sample kept after the inspection, so a dispute can be re-tested (FR-QC-020)."""

    _name = 'mos.lab.sample'
    _inherit = ['mos.audited']
    _description = 'MOS retention sample'
    _order = 'retain_until, id'

    name = fields.Char(readonly=True, copy=False, default='New')
    inspection_id = fields.Many2one('mos.lab.inspection', required=True, ondelete='restrict')
    product_id = fields.Many2one(related='inspection_id.product_id', store=True)
    site_id = fields.Many2one(related='inspection_id.site_id', store=True)
    stored_on = fields.Date(default=fields.Date.context_today, required=True)
    location = fields.Char(string='Shelf / location')
    retain_until = fields.Date(required=True)
    state = fields.Selection([('retained', 'Retained'), ('discarded', 'Discarded')], default='retained', required=True, readonly=True)
    discarded_by_id = fields.Many2one('res.users', readonly=True)
    discard_reason = fields.Char(readonly=True)
    due = fields.Boolean(compute='_compute_due', search='_search_due', string='Due for disposal')

    @api.model_create_multi
    def create(self, vals_list):
        days = self.env['ir.config_parameter'].sudo().get_int('mos_lab.retention_days', 90)
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.lab.sample')
            if not vals.get('retain_until'):
                stored = fields.Date.to_date(vals.get('stored_on')) or fields.Date.context_today(self)
                vals['retain_until'] = stored + timedelta(days=days)
        return super().create(vals_list)

    @api.depends('state', 'retain_until')
    def _compute_due(self):
        today = fields.Date.context_today(self)
        for sample in self:
            sample.due = sample.state == 'retained' and sample.retain_until <= today

    def _search_due(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [('state', '=', 'retained'), ('retain_until', '<=', today)]
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(self.env._('Unsupported search on due.'))
        values = [value] if isinstance(value, bool) else list(value)
        positive = (operator in ('=', 'in')) == (True in values)
        return domain if positive else ['!', '&'] + domain

    def action_discard(self, reason=''):
        today = fields.Date.context_today(self)
        for sample in self:
            if sample.state == 'discarded':
                raise UserError(self.env._('This sample is already discarded.'))
            if sample.retain_until > today:
                if not reason:
                    raise UserError(self.env._('The retention period has not ended: a reason is required to discard early.'))
                if not self.env.user.has_group('mos_lab.group_mos_lab_manager'):
                    raise UserError(self.env._('Only a lab manager can discard a sample before its retention period ends.'))
            sample.write({'state': 'discarded', 'discarded_by_id': self.env.uid, 'discard_reason': reason})


class MosLabCoa(models.Model):
    """Certificate of analysis for an outbound delivery, issued from a decided inspection (FR-QC-021).

    When the policy ``mos_lab.require_coa`` is on, an outbound vehicle cannot leave without one.
    """

    _name = 'mos.lab.coa'
    _inherit = ['mos.posted']
    _description = 'MOS certificate of analysis'
    _order = 'id desc'
    _mos_final_states = ('issued', 'revoked')
    _mos_editable_after_post = {'state', 'revoke_reason', 'revoked_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='restrict', index=True)
    inspection_id = fields.Many2one('mos.lab.inspection', required=True, ondelete='restrict')
    site_id = fields.Many2one(related='visit_id.site_id', store=True)
    partner_id = fields.Many2one(related='visit_id.partner_id', string='Customer')
    product_id = fields.Many2one(related='inspection_id.product_id', store=True)
    grade_id = fields.Many2one('mos.lab.grade', readonly=True)
    score = fields.Float(readonly=True, digits=(5, 1))
    results_text = fields.Text(readonly=True)
    state = fields.Selection([('issued', 'Issued'), ('revoked', 'Revoked')], default='issued', required=True, readonly=True)
    issued_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    revoke_reason = fields.Char(readonly=True)
    revoked_by_id = fields.Many2one('res.users', readonly=True)

    @api.model
    def issue(self, inspection, visit):
        if inspection.state != 'decided' or inspection.decision == 'reject':
            raise UserError(self.env._('A certificate can only be issued from an accepted, decided inspection.'))
        lines = []
        for line, result in inspection._current_results().items():
            unit = f' {line.unit}' if line.unit else ''
            lines.append(f'{line.name}: {result.value:g}{unit} (limits {line.min_value:g} to {line.max_value:g}) {"PASS" if result.passed else "FAIL"}')
        return self.create(
            {
                'visit_id': visit.id,
                'inspection_id': inspection.id,
                'grade_id': inspection.grade_id.id,
                'score': inspection.score,
                'results_text': '\n'.join(lines),
            }
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.lab.coa')
        return super().create(vals_list)

    def action_revoke(self, reason):
        if not reason:
            raise UserError(self.env._('A reason is required to revoke a certificate.'))
        for coa in self:
            if coa.state != 'issued':
                raise UserError(self.env._('Only an issued certificate can be revoked.'))
            coa.write({'state': 'revoked', 'revoke_reason': reason, 'revoked_by_id': self.env.uid})
            self.env['mos.audit.event'].record('coa_revoked', coa, f'{coa.name}: {reason}')


class MosLabReport(models.Model):
    """Lab quality over time: volume tested, results, deductions and rejections (FR-QC-030). SQL view."""

    _name = 'mos.lab.report'
    _description = 'MOS lab report'
    _auto = False
    _order = 'day desc'

    inspection_id = fields.Many2one('mos.lab.inspection', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    site_id = fields.Many2one('mos.site', readonly=True)
    day = fields.Date(readonly=True)
    state = fields.Selection([('open', 'Open'), ('tested', 'Tested'), ('decided', 'Decided')], readonly=True)
    suggested_outcome = fields.Selection([('incomplete', 'Incomplete'), ('pass', 'Pass'), ('fail', 'Fail')], readonly=True)
    decision = fields.Selection([('accept', 'Accept'), ('accept_deduction', 'Accept with deduction'), ('reject', 'Reject')], readonly=True)
    inspections = fields.Integer(readonly=True, aggregator='sum')
    score = fields.Float(readonly=True, aggregator='avg')
    net_weight = fields.Float(string='Net weight (kg)', readonly=True, aggregator='sum')
    deducted_kg = fields.Float(string='Deducted (kg)', readonly=True, aggregator='sum')
    rejected_kg = fields.Float(string='Rejected (kg)', readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT i.id AS id,
                       i.id AS inspection_id,
                       i.product_id,
                       i.site_id,
                       i.create_date::date AS day,
                       i.state,
                       i.suggested_outcome,
                       i.decision,
                       1 AS inspections,
                       i.score,
                       COALESCE(t.net_weight, 0) AS net_weight,
                       CASE WHEN i.decision = 'reject' THEN 0
                            ELSE COALESCE(t.net_weight, 0) - COALESCE(i.payable_qty, 0) END AS deducted_kg,
                       CASE WHEN i.decision = 'reject' THEN COALESCE(t.net_weight, 0) ELSE 0 END AS rejected_kg
                  FROM mos_lab_inspection i
             LEFT JOIN mos_gate_visit v ON v.id = i.visit_id
             LEFT JOIN mos_weigh_ticket t ON t.id = v.ticket_id
            )
            """
        )


class MosLabDecideWizard(models.TransientModel):
    """Lets the usage decision be made from the inspection form (the model method does the checks)."""

    _name = 'mos.lab.decide.wizard'
    _description = 'MOS usage decision'

    inspection_id = fields.Many2one('mos.lab.inspection', required=True, readonly=True)
    suggested_outcome = fields.Selection(related='inspection_id.suggested_outcome')
    auto_deduction_pct = fields.Float(related='inspection_id.auto_deduction_pct', string='Rule-based deduction (%)')
    decision = fields.Selection([('accept', 'Accept'), ('accept_deduction', 'Accept with deduction'), ('reject', 'Reject')], required=True, default='accept')
    deduction_pct = fields.Float(string='Deduction (%)', help='Empty uses the rule-based deduction.')
    note = fields.Text()

    def action_decide(self):
        self.ensure_one()
        self.inspection_id.decide(self.decision, self.deduction_pct, self.note or '')
        return {'type': 'ir.actions.act_window_close'}


class MosLabInvalidateWizard(models.TransientModel):
    _name = 'mos.lab.invalidate.wizard'
    _description = 'MOS invalidate a result'

    result_id = fields.Many2one('mos.lab.result', required=True, readonly=True)
    cause = fields.Char(required=True)

    def action_invalidate(self):
        self.ensure_one()
        self.result_id.action_invalidate(self.cause)
        return {'type': 'ir.actions.act_window_close'}


class MosLabInspectionActions(models.Model):
    _inherit = 'mos.lab.inspection'

    def action_open_decide(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Usage decision'),
            'res_model': 'mos.lab.decide.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_inspection_id': self.id},
        }
