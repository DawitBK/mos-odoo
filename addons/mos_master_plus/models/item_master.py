from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .masters import MATERIAL_TYPES


class ProductTemplate(models.Model):
    """The item master of the client (page 16): a material id of its own, a material type, and whether a delivery of it is inspected.
    Whether it is inspected is kept where quality control keeps it, in the QC policy of the item, so there is one answer."""

    _inherit = 'product.template'

    mos_material_id = fields.Char(string='Material ID', readonly=True, copy=False, index=True)
    mos_material_type = fields.Selection(MATERIAL_TYPES, string='Material type', default='other', required=True)
    mos_shelf_life_days = fields.Integer(string='Shelf life (days)', help='How long the item keeps from the day it is made. Zero means it does not expire; a lot of it gets its expiry date from this.')
    mos_pack_tare_kg = fields.Float(string='Packaging tare (kg)', digits=(8, 3), help='What the packaging of one unit weighs, taken off when goods are weighed in it.')
    mos_inspection_required = fields.Boolean(string='Inspection required', compute='_compute_mos_inspection_required', inverse='_inverse_mos_inspection_required', help='A delivery of this item is inspected by quality control. Switching it off waives the inspection, as the QC policy of the item does.')

    _mos_material_id_uniq = models.Constraint('unique(mos_material_id)', 'This material ID is already used.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('mos_material_id'):
                vals['mos_material_id'] = self.env['ir.sequence'].next_by_code('mos.material')
        return super().create(vals_list)

    @api.constrains('mos_shelf_life_days')
    def _check_shelf_life(self):
        for template in self:
            if template.mos_shelf_life_days < 0:
                raise ValidationError(self.env._('The shelf life cannot be negative.'))

    @api.depends('product_variant_ids')
    def _compute_mos_inspection_required(self):
        policies = self.env['mos.lab.policy'].sudo()
        for template in self:
            variant = template.product_variant_ids[:1]
            policy = policies.for_product(variant) if variant else False
            template.mos_inspection_required = policy.inspection_required if policy else True

    def _inverse_mos_inspection_required(self):
        policies = self.env['mos.lab.policy'].sudo()
        for template in self:
            for variant in template.product_variant_ids:
                own = policies.search([('product_id', '=', variant.id)], limit=1)
                if own:
                    own.inspection_required = template.mos_inspection_required
                elif not template.mos_inspection_required:
                    policies.create({'product_id': variant.id, 'inspection_required': False})


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.constrains('default_code')
    def _check_code_unique(self):
        """Item codes are unique across every item, archived ones included (FR-MD-003)."""
        for variant in self.filtered('default_code'):
            if self.with_context(active_test=False).search_count([('default_code', '=ilike', variant.default_code), ('id', '!=', variant.id)]):
                raise ValidationError(self.env._('The item code %s is already used.', variant.default_code))
