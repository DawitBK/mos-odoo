from odoo import api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        action = self.env.ref('mos_dashboard.action_mos_overview', raise_if_not_found=False)
        mos_group = self.env.ref('mos_core.group_mos_user', raise_if_not_found=False)
        if action and mos_group:
            for user in users.filtered(lambda u: not u.action_id and not u.share and mos_group in u.all_group_ids):
                user.sudo().action_id = action.id
        return users
