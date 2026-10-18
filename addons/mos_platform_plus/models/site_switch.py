from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResUsers(models.Model):
    """A person who works at several sites picks the one they are working at; new records default to it (FR-PLT-019)."""

    _inherit = 'res.users'

    mos_active_site_id = fields.Many2one(
        'mos.site',
        string='Working at',
        domain="[('id', 'in', mos_site_ids)]",
        user_writeable=True,
        help='The site new records are made for. It must be one of your sites.',
    )

    @api.constrains('mos_active_site_id')
    def _check_active_site(self):
        for user in self:
            if user.mos_active_site_id and user.mos_active_site_id not in user.mos_site_ids:
                raise ValidationError(self.env._('%(user)s does not work at %(site)s.', user=user.name, site=user.mos_active_site_id.display_name))

    def _mos_current_site(self):
        """The site this person is working at: the chosen one, else their first, else none."""
        self.ensure_one()
        site = self.sudo().mos_active_site_id
        if site and site in self.sudo().mos_site_ids:
            return site
        return self.sudo().mos_site_ids[:1]

    def write(self, vals):
        result = super().write(vals)
        if 'mos_site_ids' in vals:
            for user in self.sudo():
                if user.mos_active_site_id and user.mos_active_site_id not in user.mos_site_ids:
                    user.mos_active_site_id = False
        return result


class MosSite(models.Model):
    _inherit = 'mos.site'

    def action_work_here(self):
        """Make this the site the person is working at."""
        self.ensure_one()
        self.env.user.sudo().write({'mos_active_site_id': self.id})
        return {'type': 'ir.actions.client', 'tag': 'reload'}
