from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.http import request


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


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def default_get(self, fields_list):
        """A new record is made for the site the person works at, wherever the model has a site and nothing else was asked for."""
        result = super().default_get(fields_list)
        field = self._fields.get('site_id')
        if 'site_id' in fields_list and field and field.type == 'many2one' and field.comodel_name == 'mos.site' and 'default_site_id' not in self.env.context and not result.get('site_id'):
            site = self.env.user._mos_current_site()
            if site:
                result['site_id'] = site.id
        return result


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        info = super().session_info()
        user = self.env.user
        if user._is_internal() and user.sudo().mos_site_ids:
            sites = user.sudo().mos_site_ids
            current = user._mos_current_site()
            info['mos_operation_unit'] = {
                'id': current.id,
                'name': current.name,
                'several': len(sites) > 1,
                'pick': len(sites) > 1 and not (request and request.session.get('mos_unit_asked')),
            }
        return info


class MosOperationUnit(models.TransientModel):
    """The operation unit (site) a person works at, asked for after sign-in and changed from the user menu (decision D2, FR-PLT-019)."""

    _name = 'mos.operation.unit'
    _description = 'MOS operation unit'

    site_id = fields.Many2one('mos.site', string='Operation unit', required=True, default=lambda self: self.env.user._mos_current_site(), domain="[('id', 'in', allowed_site_ids)]")
    allowed_site_ids = fields.Many2many('mos.site', compute='_compute_allowed')

    @api.depends_context('uid')
    def _compute_allowed(self):
        for wizard in self:
            wizard.allowed_site_ids = self.env.user.sudo().mos_site_ids

    @api.model
    def mark_asked(self):
        """The picker was shown: it is not shown again in this sign-in, whatever the person chooses."""
        if request:
            request.session['mos_unit_asked'] = True
        return True

    def action_confirm(self):
        self.ensure_one()
        user = self.env.user
        if self.site_id not in user.sudo().mos_site_ids:
            raise ValidationError(self.env._('You do not work at %s.', self.site_id.display_name))
        user.sudo().write({'mos_active_site_id': self.site_id.id})
        self.mark_asked()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
