from odoo import fields, models


class MosServiceCategory(models.Model):
    """What a supplier provides besides goods: transport, repairs, laboratory work, packaging printing... (FR-MD-013)."""

    _name = 'mos.service.category'
    _description = 'MOS product and service category'
    _order = 'name'

    name = fields.Char(required=True)
    kind = fields.Selection([('product', 'Product'), ('service', 'Service')], default='service', required=True)
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('unique(name)', 'This category already exists.')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_service_category_ids = fields.Many2many('mos.service.category', 'mos_partner_service_category_rel', 'partner_id', 'category_id', string='Provides')
    mos_supplied_category_ids = fields.Many2many('product.category', 'mos_partner_supplied_category_rel', 'partner_id', 'category_id', string='Material categories supplied')


class MosSupplierRegistration(models.TransientModel):
    _inherit = 'mos.supplier.registration'

    category_ids = fields.Many2many('mos.service.category', 'mos_registration_service_category_rel', 'registration_id', 'category_id', string='Products and services')

    def action_register(self):
        action = super().action_register()
        if self.category_ids and action.get('res_model') == 'res.partner':
            self.env['res.partner'].browse(action['res_id']).mos_service_category_ids = self.category_ids
        return action
