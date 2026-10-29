from odoo import api, fields, models

from .masters import MATERIAL_TYPES


class ProductTemplate(models.Model):
    """The item master of the client (page 16): a material id of its own, a material type, and whether a delivery of it is inspected.
    Whether it is inspected is kept where quality control keeps it, in the QC policy of the item, so there is one answer."""

    _inherit = 'product.template'

    mos_material_id = fields.Char(string='Material ID', readonly=True, copy=False, index=True)
    mos_material_type = fields.Selection(MATERIAL_TYPES, string='Material type', default='other', required=True)
    mos_inspection_required = fields.Boolean(string='Inspection required', compute='_compute_mos_inspection_required', inverse='_inverse_mos_inspection_required', help='A delivery of this item is inspected by quality control. Switching it off waives the inspection, as the QC policy of the item does.')

    _mos_material_id_uniq = models.Constraint('unique(mos_material_id)', 'This material ID is already used.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('mos_material_id'):
                vals['mos_material_id'] = self.env['ir.sequence'].next_by_code('mos.material')
        return super().create(vals_list)

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
