from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosLabSpec(models.Model):
    """A specification for a product: parameters with limits. Versioned and approved; an approved
    version is never changed, a change is a new version (FR-QC-002, FR-QC-003)."""

    _name = 'mos.lab.spec'
    _inherit = ['mos.posted']
    _description = 'MOS specification'
    _order = 'product_id, version desc'
    _mos_final_states = ('approved', 'retired')
    _mos_editable_after_post = {'state'}

    name = fields.Char(compute='_compute_name', store=True)
    product_id = fields.Many2one('product.product', required=True, ondelete='restrict')
    version = fields.Integer(default=1, required=True, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('retired', 'Retired')], default='draft', required=True, readonly=True)
    line_ids = fields.One2many('mos.lab.spec.line', 'spec_id', string='Parameters')
    grade_ids = fields.One2many('mos.lab.grade', 'spec_id', string='Grades')
    rule_count = fields.Integer(compute='_compute_rule_count')

    def _compute_rule_count(self):
        for spec in self:
            spec.rule_count = len(spec.line_ids.rule_ids)

    def action_view_rules(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Deduction rules'),
            'res_model': 'mos.lab.deduction.rule',
            'view_mode': 'list',
            'domain': [('spec_id', '=', self.id)],
        }

    _version_uniq = models.Constraint('unique(product_id, version)', 'This specification version already exists.')

    @api.depends('product_id', 'version')
    def _compute_name(self):
        for spec in self:
            spec.name = f'{spec.product_id.display_name or "?"} v{spec.version}'

    def action_approve(self):
        for spec in self:
            if not spec.line_ids:
                raise UserError(self.env._('A specification needs at least one parameter before it can be approved.'))
            # The spec in force is the single approved one: an earlier approved version is retired.
            self.search([('product_id', '=', spec.product_id.id), ('state', '=', 'approved'), ('id', '!=', spec.id)]).write({'state': 'retired'})
            spec.write({'state': 'approved'})
            self.env['mos.audit.event'].record('spec_approved', spec, spec.name)

    def action_new_version(self):
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(self.env._('A draft is edited in place; only an approved version needs a new version.'))
        top = self.search([('product_id', '=', self.product_id.id)], order='version desc', limit=1)
        new = self.create({'product_id': self.product_id.id, 'version': top.version + 1})
        for line in self.line_ids:
            line.copy({'spec_id': new.id})
        for grade in self.grade_ids:
            grade.copy({'spec_id': new.id})
        return new

    @api.model
    def in_force(self, product):
        return self.search([('product_id', '=', product.id), ('state', '=', 'approved')], limit=1)


class MosLabSpecLine(models.Model):
    _name = 'mos.lab.spec.line'
    _description = 'MOS specification parameter'
    _order = 'sequence, id'

    spec_id = fields.Many2one('mos.lab.spec', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Parameter', required=True)
    unit = fields.Char()
    min_value = fields.Float(digits=(12, 3))
    max_value = fields.Float(digits=(12, 3))
    weight = fields.Float(default=1.0, help='How much this parameter counts in the quality score.')
    rule_ids = fields.One2many('mos.lab.deduction.rule', 'line_id', string='Deduction bands', copy=True)

    @api.constrains('weight')
    def _check_weight(self):
        for line in self:
            if line.weight <= 0:
                raise ValidationError(self.env._('Parameter %s: the weight must be above zero.', line.name))

    @api.constrains('min_value', 'max_value')
    def _check_limits(self):
        for line in self:
            if line.max_value < line.min_value:
                raise ValidationError(self.env._('Parameter %s: the maximum is below the minimum.', line.name))

    def _guard_parent(self):
        for line in self:
            if line.spec_id.state != 'draft':
                raise UserError(self.env._('Parameters of an approved specification cannot change; create a new version.'))

    @api.model_create_multi
    def create(self, vals_list):
        specs = self.env['mos.lab.spec'].browse([v['spec_id'] for v in vals_list if v.get('spec_id')])
        if any(s.state != 'draft' for s in specs):
            raise UserError(self.env._('Parameters of an approved specification cannot change; create a new version.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard_parent()
        return super().write(vals)

    def unlink(self):
        self._guard_parent()
        return super().unlink()
