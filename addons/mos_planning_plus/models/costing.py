from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MosWorkCentre(models.Model):
    _inherit = 'mos.work.centre'

    labour_rate_h = fields.Float(string='Labour rate (per person-hour)', digits=(12, 2))
    overhead_rate_h = fields.Float(string='Overhead rate (per hour)', digits=(12, 2), help='What running this centre for an hour absorbs of rent, power, depreciation and supervision.')


class MosLabourLog(models.Model):
    """Who worked on a work order and for how long, at what rate (FR-MFG-010)."""

    _name = 'mos.labour.log'
    _inherit = ['mos.audited']
    _description = 'MOS labour log'
    _order = 'date desc, id desc'

    order_id = fields.Many2one('mos.work.order', required=True, ondelete='cascade')
    date = fields.Date(default=fields.Date.context_today, required=True)
    worker = fields.Char(required=True)
    hours = fields.Float(required=True, digits=(8, 2))
    rate = fields.Float(string='Rate per hour', digits=(12, 2))
    cost = fields.Float(compute='_compute_cost', store=True, digits=(14, 2))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'rate' not in vals and vals.get('order_id'):
                vals['rate'] = self.env['mos.work.order'].browse(vals['order_id']).work_centre_id.labour_rate_h
        return super().create(vals_list)

    @api.depends('hours', 'rate')
    def _compute_cost(self):
        for log in self:
            log.cost = round(log.hours * log.rate, 2)

    @api.constrains('hours', 'rate')
    def _check(self):
        for log in self:
            if log.hours <= 0 or log.hours > 24 or log.rate < 0:
                raise ValidationError(self.env._('Hours are above zero and at most 24, and the rate cannot be negative.'))


class MosWorkOrder(models.Model):
    """Conversion cost and its variances: usage, yield, labour and overhead absorption (FR-MFG-010).

    Sign convention: a positive variance is unfavourable (it cost more than the standard).
    """

    _inherit = 'mos.work.order'

    crew_size = fields.Integer(default=1, help='People the standard assumes on this order.')
    labour_log_ids = fields.One2many('mos.labour.log', 'order_id')
    overhead_incurred = fields.Float(string='Overhead incurred', digits=(14, 2), help='Entered by finance when the order is finished.')
    labour_hours = fields.Float(compute='_compute_costing', digits=(12, 2))
    labour_cost = fields.Float(compute='_compute_costing', digits=(14, 2))
    standard_labour = fields.Float(compute='_compute_costing', digits=(14, 2))
    overhead_absorbed = fields.Float(compute='_compute_costing', digits=(14, 2))
    usage_variance = fields.Float(compute='_compute_costing', digits=(14, 2))
    yield_variance = fields.Float(compute='_compute_costing', digits=(14, 2))
    labour_variance = fields.Float(compute='_compute_costing', digits=(14, 2))
    absorption_variance = fields.Float(compute='_compute_costing', digits=(14, 2))
    conversion_cost_per_kg = fields.Float(compute='_compute_costing', digits=(12, 4), string='Conversion cost per kg of flour')

    @api.constrains('crew_size')
    def _check_crew(self):
        for order in self:
            if order.crew_size < 1:
                raise ValidationError(self.env._('A crew has at least one person.'))

    def _mos_standard_wheat_price(self):
        self.ensure_one()
        return sum(line.share_pct / 100.0 * line.wheat_id.standard_price for line in self.recipe_id.line_ids)

    def _mos_variances(self, wheat_kg, flour_kg):
        """The figures for a given actual output, so the arithmetic can be checked without finishing a run."""
        self.ensure_one()
        centre = self.work_centre_id
        hours = self.planned_minutes / 60.0
        standard_labour = round(hours * centre.labour_rate_h * self.crew_size, 2)
        absorbed = round(hours * centre.overhead_rate_h, 2)
        labour_cost = round(sum(self.labour_log_ids.mapped('cost')), 2)
        usage = round((wheat_kg - self.planned_kg) * self._mos_standard_wheat_price(), 2) if wheat_kg else 0.0
        target = self.recipe_id.target_extraction_pct
        actual_pct = flour_kg / wheat_kg * 100.0 if wheat_kg else target
        yield_var = round((target - actual_pct) / 100.0 * wheat_kg * self.product_id.standard_price, 2) if wheat_kg else 0.0
        return {
            'labour_hours': round(sum(self.labour_log_ids.mapped('hours')), 2),
            'labour_cost': labour_cost,
            'standard_labour': standard_labour,
            'overhead_absorbed': absorbed,
            'usage_variance': usage,
            'yield_variance': yield_var,
            'labour_variance': round(labour_cost - standard_labour, 2),
            'absorption_variance': round(self.overhead_incurred - absorbed, 2),
            'conversion_cost_per_kg': round((labour_cost + self.overhead_incurred) / flour_kg, 4) if flour_kg else 0.0,
        }

    @api.depends('labour_log_ids.cost', 'labour_log_ids.hours', 'overhead_incurred', 'planned_minutes', 'crew_size', 'planned_kg', 'actual_wheat_kg', 'actual_flour_kg', 'work_centre_id.labour_rate_h', 'work_centre_id.overhead_rate_h')
    def _compute_costing(self):
        for order in self:
            for name, value in order._mos_variances(order.actual_wheat_kg, order.actual_flour_kg).items():
                order[name] = value
