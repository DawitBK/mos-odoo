import zoneinfo

from odoo import api, fields, models
from odoo.exceptions import ValidationError


def _tz_selection(self):
    return [(tz, tz) for tz in sorted(zoneinfo.available_timezones())]


class MosSite(models.Model):
    """A site: the unit that scopes documents, users and business dates (FR-PLT-002, 004, 012).

    Odoo has no site object, so a site is a thin record tied to one warehouse. Every MOS document
    carries its site, and record rules restrict users to the sites they are assigned to.
    """

    _name = 'mos.site'
    _description = 'MOS site'
    _order = 'code'

    name = fields.Char(required=True)
    code = fields.Char(required=True, size=10)
    tz = fields.Selection(_tz_selection, string='Time zone', required=True, default='Africa/Addis_Ababa')
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', ondelete='restrict')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    user_ids = fields.Many2many('res.users', 'mos_site_users_rel', 'site_id', 'user_id', string='Users')

    _code_uniq = models.Constraint('unique(code, company_id)', 'The site code must be unique per company.')

    def business_date(self, when=None):
        """Today's date in the site's time zone, computed on the server (FR-PLT-004)."""
        self.ensure_one()
        moment = when or fields.Datetime.now()
        utc = moment.replace(tzinfo=zoneinfo.ZoneInfo('UTC'))
        return utc.astimezone(zoneinfo.ZoneInfo(self.tz)).date()

    @api.constrains('active')
    def _check_deactivation(self):
        for site in self.filtered(lambda s: not s.active):
            visits = self.env.get('mos.gate.visit')
            if visits is not None and visits.sudo().search_count(
                [('site_id', '=', site.id), ('state', 'not in', ('exited', 'cancelled'))]
            ):
                raise ValidationError(self.env._('Site %s still has open gate visits and cannot be deactivated.', site.name))
