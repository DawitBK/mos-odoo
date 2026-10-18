from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosItemConversion(models.Model):
    """What a named pack of one item holds, in the item's own unit: 1 bag of wheat = 50 kg, 1 bale of packaging = 500 bags. Once any
    document has used a conversion it never changes; a change is a new version with an effective date (FR-MD-002)."""

    _name = 'mos.item.conversion'
    _inherit = ['mos.audited']
    _description = 'MOS item conversion'
    _order = 'product_id, unit_name, effective_from desc'

    product_id = fields.Many2one('product.product', required=True)
    unit_name = fields.Char(string='Pack', required=True, help='bag, bale, sack, drum...')
    factor = fields.Float(string='Holds (item units)', required=True, digits=(14, 4))
    effective_from = fields.Date(required=True, default=fields.Date.context_today)
    effective_to = fields.Date(readonly=True)
    use_count = fields.Integer(readonly=True, help='How many documents have used this conversion.')
    active = fields.Boolean(default=True)

    @api.constrains('factor')
    def _check_factor(self):
        for rec in self:
            if rec.factor <= 0:
                raise ValidationError(self.env._('A pack holds more than nothing.'))

    @api.constrains('product_id', 'unit_name', 'effective_from')
    def _check_dates(self):
        for rec in self:
            clash = self.search_count([('product_id', '=', rec.product_id.id), ('unit_name', '=ilike', rec.unit_name), ('effective_from', '=', rec.effective_from), ('id', '!=', rec.id)])
            if clash:
                raise ValidationError(self.env._('There is already a conversion for %(unit)s of %(product)s from %(date)s.', unit=rec.unit_name, product=rec.product_id.display_name, date=rec.effective_from))

    def write(self, vals):
        if self.filtered('use_count') and {'product_id', 'unit_name', 'factor', 'effective_from'} & set(vals):
            raise UserError(self.env._('A conversion that documents have used cannot be changed: add a new one with an effective date.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered('use_count'):
            raise UserError(self.env._('A conversion that documents have used cannot be deleted.'))
        return super().unlink()

    @api.model
    def in_force(self, product, unit_name, date=None):
        date = date or fields.Date.context_today(self)
        return self.search(
            [('product_id', '=', product.id), ('unit_name', '=ilike', unit_name), ('effective_from', '<=', date), ('active', '=', True)],
            order='effective_from desc',
            limit=1,
        )

    @api.model
    def convert(self, product, qty, unit_name, date=None, use=False):
        """`qty` packs of `unit_name` expressed in the item's own unit. With `use=True` the conversion is marked as used by a document."""
        conversion = self.in_force(product, unit_name, date)
        if not conversion:
            raise UserError(self.env._('No conversion is set for %(unit)s of %(product)s.', unit=unit_name, product=product.display_name))
        if use:
            conversion.sudo().use_count += 1
        return qty * conversion.factor

    def action_new_version(self, factor, effective_from):
        """Replace the factor from a date: this conversion ends the day before, a new one starts."""
        self.ensure_one()
        if effective_from <= self.effective_from:
            raise UserError(self.env._('The new conversion must start after %s.', self.effective_from))
        new = self.create({'product_id': self.product_id.id, 'unit_name': self.unit_name, 'factor': factor, 'effective_from': effective_from})
        self.sudo().write({'effective_to': effective_from - timedelta(days=1)})
        return new
