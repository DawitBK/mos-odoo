from odoo import api, models


class ProductCategory(models.Model):
    """Stock is valued at a moving weighted average per item (FR-INV-010): Odoo's costing method of a category is average, not its default standard."""

    _inherit = 'product.category'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('property_cost_method', 'average')
        return super().create(vals_list)

    @api.model
    def _mos_set_average_costing(self):
        """Put every category (and the root one) on the average method; used once when the addon is installed or upgraded."""
        categories = self.with_context(active_test=False).sudo().search([])
        categories.filtered(lambda c: c.property_cost_method != 'average').write({'property_cost_method': 'average'})
        return len(categories)
