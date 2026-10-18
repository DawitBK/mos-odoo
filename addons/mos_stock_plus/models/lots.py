from datetime import timedelta

from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError


class MosLotCounter(models.Model):
    """The last batch number given on each day, so `BATCH-20261018-01`, `-02` ... never repeat and never skip (FR-INV-004)."""

    _name = 'mos.lot.counter'
    _description = 'MOS lot counter'

    day = fields.Date(required=True, index=True)
    last = fields.Integer(readonly=True)

    _day_uniq = models.Constraint('unique(day)', 'One counter per day.')

    @api.model
    def next_number(self, day):
        """Atomic: two simultaneous receipts cannot get the same number."""
        self.env.cr.execute(
            """
            INSERT INTO mos_lot_counter (day, last, create_uid, create_date, write_uid, write_date)
                 VALUES (%s, 1, %s, NOW() AT TIME ZONE 'UTC', %s, NOW() AT TIME ZONE 'UTC')
            ON CONFLICT (day) DO UPDATE SET last = mos_lot_counter.last + 1
              RETURNING last
            """,
            (day, self.env.uid, self.env.uid),
        )
        return self.env.cr.fetchone()[0]


class MosLotAttribute(models.Model):
    """What a lot of a product category must record: moisture, protein, a supplier certificate number... (FR-MD-004)."""

    _name = 'mos.lot.attribute'
    _inherit = ['mos.audited']
    _description = 'MOS lot attribute'
    _order = 'category_id, sequence, name'

    category_id = fields.Many2one('product.category', required=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    kind = fields.Selection([('number', 'Number'), ('text', 'Text'), ('date', 'Date'), ('choice', 'One of a list')], default='text', required=True)
    choices = fields.Char(help='For "one of a list": the allowed values, separated by commas.')
    required = fields.Boolean()
    min_value = fields.Float()
    max_value = fields.Float()
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('unique(category_id, name)', 'This category already has an attribute of that name.')

    @api.constrains('kind', 'choices', 'min_value', 'max_value')
    def _check(self):
        for attr in self:
            if attr.kind == 'choice' and not (attr.choices or '').strip():
                raise ValidationError(self.env._('List the allowed values for %s.', attr.name))
            if attr.kind == 'number' and attr.max_value and attr.min_value > attr.max_value:
                raise ValidationError(self.env._('The minimum of %s is above its maximum.', attr.name))


class MosLotAttributeValue(models.Model):
    _name = 'mos.lot.attribute.value'
    _description = 'MOS lot attribute value'
    _order = 'attribute_id'

    lot_id = fields.Many2one('stock.lot', required=True, ondelete='cascade')
    attribute_id = fields.Many2one('mos.lot.attribute', required=True, domain="[('category_id', 'parent_of', parent.mos_category_id)]")
    value = fields.Char(required=True)

    _once = models.Constraint('unique(lot_id, attribute_id)', 'An attribute is recorded once per lot.')

    @api.constrains('value', 'attribute_id')
    def _check_value(self):
        for rec in self:
            attr, value = rec.attribute_id, (rec.value or '').strip()
            if attr.kind == 'number':
                try:
                    number = float(value)
                except ValueError:
                    raise ValidationError(self.env._('%(a)s must be a number, not "%(v)s".', a=attr.name, v=value)) from None
                if attr.min_value and number < attr.min_value or attr.max_value and number > attr.max_value:
                    raise ValidationError(self.env._('%(a)s of %(v)s is outside %(lo)s to %(hi)s.', a=attr.name, v=value, lo=attr.min_value, hi=attr.max_value))
            elif attr.kind == 'date':
                try:
                    fields.Date.to_date(value)
                except (ValueError, TypeError):
                    raise ValidationError(self.env._('%(a)s must be a date (YYYY-MM-DD).', a=attr.name)) from None
            elif attr.kind == 'choice' and value not in [c.strip() for c in (attr.choices or '').split(',')]:
                raise ValidationError(self.env._('%(a)s must be one of: %(c)s.', a=attr.name, c=attr.choices))


class StockLot(models.Model):
    _inherit = 'stock.lot'

    name = fields.Char(default='/', help='Left as / the system gives it the next batch number: BATCH-yyyymmdd-nn.')
    mos_supplier_lot = fields.Char(string="Supplier's lot")
    mos_mfg_date = fields.Date(string='Manufactured')
    mos_expiry_date = fields.Date(string='Expires')
    mos_category_id = fields.Many2one(related='product_id.categ_id', string='Category')
    mos_attribute_ids = fields.One2many('mos.lot.attribute.value', 'lot_id', string='Attributes')
    mos_parent_lot_id = fields.Many2one('stock.lot', string='Split from', readonly=True)
    mos_status = fields.Selection([('available', 'Available'), ('quarantine', 'Quarantine'), ('blocked', 'Blocked'), ('none', 'No stock')], compute='_compute_mos_status')
    mos_days_to_expiry = fields.Integer(compute='_compute_mos_status')

    @api.model
    def _mos_next_name(self, day=None):
        day = day or fields.Date.context_today(self)
        return 'BATCH-%s-%02d' % (day.strftime('%Y%m%d'), self.env['mos.lot.counter'].sudo().next_number(day))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') in ('/', False, ''):
                vals['name'] = self._mos_next_name()
        return super().create(vals_list)

    @api.constrains('mos_mfg_date', 'mos_expiry_date')
    def _check_dates(self):
        for lot in self:
            if lot.mos_mfg_date and lot.mos_expiry_date and lot.mos_expiry_date < lot.mos_mfg_date:
                raise ValidationError(self.env._('Lot %s expires before it was made.', lot.name))

    @api.constrains('mos_attribute_ids', 'product_id')
    def _check_required_attributes(self):
        if not self.env['ir.config_parameter'].sudo().get_bool('mos_stock_plus.enforce_lot_attributes', False):
            return
        for lot in self:
            missing = lot._mos_missing_attributes()
            if missing:
                raise ValidationError(self.env._('Lot %(lot)s needs: %(what)s.', lot=lot.name, what=', '.join(missing.mapped('name'))))

    def _mos_missing_attributes(self):
        self.ensure_one()
        required = self.env['mos.lot.attribute'].sudo().search([('category_id', 'parent_of', self.product_id.categ_id.id), ('required', '=', True)])
        return required - self.mos_attribute_ids.attribute_id

    @api.depends('quant_ids.quantity', 'quant_ids.location_id.mos_stock_status', 'mos_expiry_date')
    def _compute_mos_status(self):
        today = fields.Date.context_today(self)
        for lot in self:
            quants = lot.sudo().quant_ids.filtered(lambda q: q.location_id.usage == 'internal' and q.quantity > 0)
            statuses = set(quants.mapped('location_id.mos_stock_status'))
            lot.mos_status = 'none' if not quants else 'available' if statuses == {'available'} else 'blocked' if 'blocked' in statuses else 'quarantine' if 'quarantine' in statuses else 'available'
            lot.mos_days_to_expiry = (lot.mos_expiry_date - today).days if lot.mos_expiry_date else 0

    def action_split(self, qty, new_name=None, location=None):
        """Relabel `qty` of this lot as a new lot in the same place (a split lot keeps its dates and records where it came from)."""
        self.ensure_one()
        quant_model = self.env['stock.quant'].sudo()
        quants = quant_model.search([('lot_id', '=', self.id), ('location_id.usage', '=', 'internal'), ('quantity', '>', 0)] + ([('location_id', '=', location.id)] if location else []), order='quantity desc')
        if not quants:
            raise UserError(self.env._('Lot %s has no stock to split.', self.name))
        quant = quants[0]
        if qty <= 0 or qty >= quant.quantity - 0.0001:
            raise UserError(self.env._('Split off more than nothing and less than the %(have)s in the lot.', have=quant.quantity))
        new = self.create(
            {
                'name': new_name or self._mos_next_name(),
                'product_id': self.product_id.id,
                'mos_supplier_lot': self.mos_supplier_lot,
                'mos_mfg_date': self.mos_mfg_date,
                'mos_expiry_date': self.mos_expiry_date,
                'mos_parent_lot_id': self.id,
                'mos_attribute_ids': [(0, 0, {'attribute_id': v.attribute_id.id, 'value': v.value}) for v in self.mos_attribute_ids],
            }
        )
        quant_model._update_available_quantity(self.product_id, quant.location_id, -qty, lot_id=self)
        quant_model._update_available_quantity(self.product_id, quant.location_id, qty, lot_id=new)
        self.env['mos.audit.event'].record('lot_split', self, f'{self.name} -> {new.name}: {qty} {self.product_id.display_name}')
        return new

    def action_trace(self):
        """This lot and everything split from it (forward), and where it came from (backward)."""
        self.ensure_one()
        root = self
        while root.mos_parent_lot_id:
            root = root.mos_parent_lot_id
        family = root
        frontier = root
        while frontier:
            frontier = self.search([('mos_parent_lot_id', 'in', frontier.ids)])
            family |= frontier
        return family


class MosLotExpiry(models.Model):
    """Lots in stock, soonest to expire first, with what is left of each (FR-INV-014)."""

    _name = 'mos.lot.expiry'
    _description = 'MOS expiry list'
    _auto = False
    _order = 'days_left, lot_id'

    lot_id = fields.Many2one('stock.lot', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    location_id = fields.Many2one('stock.location', readonly=True)
    status = fields.Selection([('available', 'Available'), ('quarantine', 'Quarantine'), ('blocked', 'Blocked')], readonly=True)
    qty = fields.Float(readonly=True, aggregator='sum')
    expiry = fields.Date(readonly=True)
    days_left = fields.Integer(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT sq.id AS id, sq.lot_id, sq.product_id, sq.location_id, sl.mos_stock_status AS status, sq.quantity AS qty,
                       lot.mos_expiry_date AS expiry, (lot.mos_expiry_date - CURRENT_DATE) AS days_left
                  FROM stock_quant sq
                  JOIN stock_lot lot ON lot.id = sq.lot_id
                  JOIN stock_location sl ON sl.id = sq.location_id
                 WHERE sl.usage = 'internal' AND sq.quantity > 0 AND lot.mos_expiry_date IS NOT NULL
            )
            """
        )

    @api.model
    def expiring(self, days=30):
        self.env.flush_all()
        return self.search([('days_left', '<=', days)])
