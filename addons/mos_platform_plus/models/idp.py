from odoo import SUPERUSER_ID, api, fields, models


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

    @api.model
    def _mos_idp_reported(self, identity):
        """The groups or roles the identity provider reported for this person, or None when it sent none (the claim is absent)."""
        claim = self.env['ir.config_parameter'].sudo().get_str('mos_auth_oidc.groups_claim', 'groups')
        if not claim or claim not in identity:
            return None
        raw = identity.get(claim) or []
        return {str(g).strip() for g in (raw if isinstance(raw, (list, tuple)) else str(raw).split(','))} - {''}

    @api.model
    def _mos_provision_from_idp(self, identity):
        """Create the account of a person the identity provider has given a MOS role, at their first sign-in. Returns the user, or an empty set.

        Off unless the system parameter `mos_platform_plus.idp_create_accounts` is 1: MOS's own rule is that an administrator creates accounts,
        and a deployment that wants the identity provider's console to decide who may use MOS says so here. Even then an account is only made
        for a person who holds at least one role that maps to a MOS role: the administrator role is never given by a mapping, so a person who
        holds only that one is still refused until a MOS administrator creates their account.
        """
        users = self.sudo()
        if self.env['ir.config_parameter'].sudo().get_str('mos_platform_plus.idp_create_accounts', '0') != '1':
            return users.browse()
        email = (identity.get('email') or '').strip().lower()
        reported = self._mos_idp_reported(identity)
        if not email or not reported:
            return users.browse()
        admin = self.env.ref('mos_core.group_mos_admin')
        maps = self.env['mos.idp.group.map'].sudo().search([('idp_group', 'in', list(reported))])
        if not (maps.mapped('group_id') - admin):
            return users.browse()
        name = (identity.get('name') or ' '.join(p for p in (identity.get('given_name'), identity.get('family_name')) if p) or email).strip()
        # A sign-in arrives on a public route, where there is no current company to default from: name the company (MOS runs one per install).
        company = self.env.ref('base.main_company', raise_if_not_found=False) or self.env['res.company'].sudo().search([], order='id', limit=1)
        # Made as the system user: a public route has no user at all, and creating one (its avatar, for a start) needs one.
        user = self.with_user(SUPERUSER_ID).with_context(no_reset_password=True).create(
            {
                'name': name,
                'login': email,
                'email': email,
                'company_id': company.id,
                'company_ids': [(6, 0, company.ids)],
                'group_ids': [(4, self.env.ref('base.group_user').id)],
            }
        )
        # The person is the one who acted (their first sign-in), so the entry is theirs.
        self.env['mos.audit.event'].with_user(user.id).sudo().record(
            'idp_account_created', user, f'{user.login}: created at the first sign-in; the identity provider gave {sorted(reported)}'
        )
        return user

    def _mos_apply_idp_claims(self, identity):
        """Give the person the roles and sites their identity-provider groups map to, and take back what a group no longer gives.

        Only roles and sites that came from the provider are ever removed: what an administrator granted by hand stays.
        The administrator role is never granted or removed here, whatever the mapping says.
        """
        self.ensure_one()
        reported = self._mos_idp_reported(identity)
        if reported is None:
            return False
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
