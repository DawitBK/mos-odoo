from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    mos_site_ids = fields.Many2many('mos.site', 'mos_site_users_rel', 'user_id', 'site_id', string='MOS sites')
    mos_employee_ref = fields.Char(string='Employee ID')

    def _mos_current_site(self):
        """The site this person works at. Here it is their first site; the platform module lets a person with several sites choose."""
        self.ensure_one()
        return self.sudo().mos_site_ids[:1]
