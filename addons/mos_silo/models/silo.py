from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MosSilo(models.Model):
    """A grain silo. Its level is never typed in: it is the quantity the stock ledger holds in the
    silo's own stock location, so receipts fill it and milling draws it down (FR-INV-019)."""

    _name = 'mos.silo'
    _inherit = ['mos.audited']
    _description = 'MOS silo'
    _order = 'code'

    name = fields.Char(required=True)
    code = fields.Char(required=True, size=12)
    site_id = fields.Many2one('mos.site', required=True)
    product_id = fields.Many2one('product.product', string='Grain', help='What this silo is meant to hold; the level counts every product in it.')
    capacity_kg = fields.Float(string='Capacity (kg)', required=True, digits=(14, 0))
    silo_type = fields.Selection([('grain', 'Grain bin'), ('flour', 'Flour bin'), ('byproduct', 'By-product bin'), ('other', 'Other')], string='Silo type', default='grain', required=True)
    description = fields.Text()
    warehouse_id = fields.Many2one(related='site_id.warehouse_id', string='Warehouse')
    min_level_kg = fields.Float(string='Minimum level (kg)', digits=(14, 0), help='Below this the silo shows as low. Zero keeps the usual thresholds.')
    max_level_kg = fields.Float(string='Maximum level (kg)', digits=(14, 0), help='A load that would take the silo above this is refused. Zero means the capacity.')
    safety_stock_kg = fields.Float(string='Safety stock (kg)', digits=(14, 0), help='What must always be left in the silo. A milling run cannot draw the silo below it.')
    location_id = fields.Many2one('stock.location', string='Stock location', readonly=True, copy=False, ondelete='restrict')
    active = fields.Boolean(default=True)

    level_kg = fields.Float(string='Level (kg)', compute='_compute_level', digits=(14, 0))
    free_kg = fields.Float(string='Free space (kg)', compute='_compute_level', digits=(14, 0))
    fill_pct = fields.Float(string='Filled (%)', compute='_compute_level', digits=(5, 1))
    status = fields.Selection(
        [('empty', 'Empty'), ('low', 'Low'), ('ok', 'In use'), ('high', 'Nearly full'), ('full', 'Full')],
        compute='_compute_level',
    )

    _code_uniq = models.Constraint('unique(code, site_id)', 'The silo code must be unique per site.')
    _capacity_positive = models.Constraint('CHECK(capacity_kg > 0)', 'A silo needs a capacity above zero.')

    @api.constrains('min_level_kg', 'max_level_kg', 'safety_stock_kg', 'capacity_kg')
    def _check_levels(self):
        for silo in self:
            if min(silo.min_level_kg, silo.max_level_kg, silo.safety_stock_kg) < 0:
                raise ValidationError(self.env._('A level cannot be negative.'))
            if silo.max_level_kg and silo.max_level_kg > silo.capacity_kg:
                raise ValidationError(self.env._('The maximum level cannot be above the capacity.'))
            if silo.min_level_kg and silo.safety_stock_kg > silo.min_level_kg:
                raise ValidationError(self.env._('The safety stock cannot be above the minimum level.'))
            if silo.max_level_kg and max(silo.min_level_kg, silo.safety_stock_kg) > silo.max_level_kg:
                raise ValidationError(self.env._('The maximum level cannot be below the minimum level or the safety stock.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('location_id'):
                site = self.env['mos.site'].browse(vals['site_id'])
                parent = site.warehouse_id.lot_stock_id
                if not parent:
                    raise ValidationError(self.env._('Site %s has no warehouse; give it one before adding silos.', site.name))
                vals['location_id'] = self.env['stock.location'].sudo().create(
                    {'name': f"Silo {vals['code']}", 'location_id': parent.id, 'usage': 'internal'}
                ).id
        return super().create(vals_list)

    @api.depends('location_id', 'capacity_kg', 'max_level_kg', 'min_level_kg')
    def _compute_level(self):
        levels = {}
        if self.location_id:
            groups = self.env['stock.quant'].sudo()._read_group(
                [('location_id', 'in', self.location_id.ids)], ['location_id'], ['quantity:sum']
            )
            levels = {loc.id: qty for loc, qty in groups}
        for silo in self:
            level = max(levels.get(silo.location_id.id, 0.0), 0.0)
            silo.level_kg = level
            ceiling = silo.max_level_kg or silo.capacity_kg  # what a load may take the silo up to
            silo.free_kg = max(ceiling - level, 0.0)
            pct = (level / silo.capacity_kg * 100.0) if silo.capacity_kg else 0.0
            silo.fill_pct = pct
            low = level < silo.min_level_kg if silo.min_level_kg else pct < 20
            silo.status = 'empty' if pct < 2 else 'low' if low else 'full' if level >= ceiling - 0.5 else 'high' if pct >= 90 else 'ok'

    def fresh(self):
        """Re-read the level from the stock ledger: it is computed from stock.quant, which this record cannot see changing."""
        self.invalidate_recordset(['level_kg', 'free_kg', 'fill_pct', 'status'])
        return self

    def action_open_stock(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Stock in %s', self.name),
            'res_model': 'stock.quant',
            'view_mode': 'list',
            'domain': [('location_id', '=', self.location_id.id)],
            'context': {'search_default_internal_loc': 1},
        }
