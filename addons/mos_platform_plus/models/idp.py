from odoo import api, fields, models


class MosIdpGroupMap(models.Model):
    """A group the identity provider reports, and the MOS role and sites it gives (FR-PLT-013)."""

    _name = 'mos.idp.group.map'
    _inherit = ['mos.audited']
    _description = 'MOS identity group mapping'
    _order = 'idp_group'

    idp_group = fields.Char(string='Group at the identity provider', required=True)
    group_id = fields.Many2one('res.groups', string='MOS role', required=True, domain=lambda self: [('privilege_id', '=', self.env.ref('mos_core.res_groups_privilege_mos').id)])
    site_ids = fields.Many2many('mos.site', string='Sites it gives access to')
    active = fields.Boolean(default=True)

    _uniq = models.Constraint('unique(idp_group, group_id)', 'This group already gives that role.')


class ResUsers(models.Model):
    _inherit = 'res.users'

    mos_idp_group_ids = fields.Many2many('res.groups', 'mos_idp_user_group_rel', 'user_id', 'group_id', string='Roles from the identity provider', readonly=True)
    mos_idp_site_ids = fields.Many2many('mos.site', 'mos_idp_user_site_rel', 'user_id', 'site_id', string='Sites from the identity provider', readonly=True)

    def _mos_apply_idp_claims(self, identity):
        """Give the person the roles and sites their identity-provider groups map to, and take back what a group no longer gives.

        Only roles and sites that came from the provider are ever removed: what an administrator granted by hand stays.
        The administrator role is never granted or removed here, whatever the mapping says.
        """
        self.ensure_one()
        claim = self.env['ir.config_parameter'].sudo().get_str('mos_auth_oidc.groups_claim', 'groups')
        if not claim or claim not in identity:
            return False
        raw = identity.get(claim) or []
        reported = {str(g).strip() for g in (raw if isinstance(raw, (list, tuple)) else str(raw).split(','))} - {''}
        maps = self.env['mos.idp.group.map'].sudo().search([('idp_group', 'in', list(reported))])
        admin = self.env.ref('mos_core.group_mos_admin')
        want_groups = maps.mapped('group_id') - admin
        want_sites = maps.mapped('site_ids')
        user = self.sudo()
        add_groups = want_groups - user.group_ids
        drop_groups = (user.mos_idp_group_ids - want_groups) - admin
        add_sites = want_sites - user.mos_site_ids
        drop_sites = user.mos_idp_site_ids - want_sites
        commands = {}
        if add_groups or drop_groups:
            commands['group_ids'] = [(4, g.id) for g in add_groups] + [(3, g.id) for g in drop_groups]
        if add_sites or drop_sites:
            commands['mos_site_ids'] = [(4, s.id) for s in add_sites] + [(3, s.id) for s in drop_sites]
        # What came from the provider is recorded so a later sign-in can take it back.
        commands['mos_idp_group_ids'] = [(6, 0, want_groups.ids)]
        commands['mos_idp_site_ids'] = [(6, 0, want_sites.ids)]
        user.write(commands)
        if add_groups or drop_groups or add_sites or drop_sites:
            self.env['mos.audit.event'].record('idp_roles_applied', user, f'{user.login}: +{len(add_groups)} roles, -{len(drop_groups)} roles, +{len(add_sites)} sites, -{len(drop_sites)} sites from {sorted(reported)}')
        return True
