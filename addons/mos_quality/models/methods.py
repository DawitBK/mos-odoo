from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval


class MosLabMethod(models.Model):
    """How a parameter is measured: the standard it follows and the steps (FR-QC-001)."""

    _name = 'mos.lab.method'
    _inherit = ['mos.audited']
    _description = 'MOS test method'
    _order = 'code'

    code = fields.Char(required=True)
    name = fields.Char(required=True)
    standard = fields.Char(string='Reference standard', help='For example ICC 110/1, AACC 44-15.')
    description = fields.Text()
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(code)', 'This method code already exists.')


class MosLabParameter(models.Model):
    """A quality parameter defined once, reused by every specification (FR-QC-001)."""

    _name = 'mos.lab.parameter'
    _inherit = ['mos.audited']
    _description = 'MOS quality parameter'
    _order = 'name'

    name = fields.Char(required=True)
    unit = fields.Char()
    method_id = fields.Many2one('mos.lab.method')
    typical_min = fields.Float(digits=(12, 3))
    typical_max = fields.Float(digits=(12, 3))
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('unique(name)', 'This parameter already exists.')


class MosLabSpecLine(models.Model):
    """A specification line may name its library parameter and method, or be calculated from other lines (FR-QC-001, 021)."""

    _inherit = 'mos.lab.spec.line'

    parameter_id = fields.Many2one('mos.lab.parameter')
    method_id = fields.Many2one('mos.lab.method')
    formula = fields.Char(help="A calculation from other lines, written with their codes: for example dry_gluten = wet_gluten * 0.33. Leave empty for a measured line.")
    code = fields.Char(help='Short name used in formulas (letters, digits and underscores).')

    @api.onchange('parameter_id')
    def _onchange_parameter(self):
        if self.parameter_id:
            self.name = self.parameter_id.name
            self.unit = self.parameter_id.unit
            self.method_id = self.parameter_id.method_id

    @api.constrains('code', 'formula')
    def _check_formula(self):
        for line in self:
            if line.code and not line.code.replace('_', '').isalnum():
                raise ValidationError(self.env._('The code %s may only hold letters, digits and underscores.', line.code))
            if line.formula:
                others = {l.code for l in line.spec_id.line_ids if l.code and l != line}
                try:
                    safe_eval(line.formula.split('=', 1)[-1], {k: 1.0 for k in others})
                except Exception as error:
                    raise ValidationError(self.env._('The formula of %(name)s cannot be evaluated: %(error)s', name=line.name, error=error))


class MosLabResult(models.Model):
    """A calculated result is filled in as soon as the lines it needs have results, and a measured result names the instrument used."""

    _inherit = 'mos.lab.result'

    instrument_id = fields.Many2one('mos.lab.instrument')
    calculated = fields.Boolean(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not rec.calculated:
                rec.inspection_id._recalculate()
        return records


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    def _recalculate(self):
        """Evaluate every calculated line whose inputs have a valid result; replace its earlier calculation."""
        for insp in self:
            if insp.state == 'decided':
                continue
            current = {line: r for line, r in insp._current_results().items()}
            values = {line.code: r.value for line, r in current.items() if line.code and not line.formula}
            for line in insp.spec_id.line_ids.filtered('formula'):
                target, _, expression = line.formula.partition('=')
                expression = expression or target
                try:
                    value = safe_eval(expression, dict(values))
                except Exception:
                    continue
                existing = insp.result_ids.filtered(lambda r: r.line_id == line and r.valid)
                if existing and abs(existing[-1].value - value) < 1e-9:
                    continue
                existing.sudo().write({'valid': False, 'invalid_cause': self.env._('Recalculated')})
                self.env['mos.lab.result'].sudo().create({'inspection_id': insp.id, 'line_id': line.id, 'value': value, 'calculated': True, 'entered_by_id': self.env.uid})
