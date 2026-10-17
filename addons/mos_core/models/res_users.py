from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    mos_site_ids = fields.Many2many('mos.site', 'mos_site_users_rel', 'user_id', 'site_id', string='MOS sites')
    mos_employee_ref = fields.Char(string='Employee ID')
