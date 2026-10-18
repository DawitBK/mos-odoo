import base64
import csv
import io

from odoo import api, fields, models
from odoo.exceptions import UserError


class MosAccessEvent(models.Model):
    """Who gave or took away which MOS role, and when. Written automatically whenever a user's groups change (FR-PLT-017)."""

    _name = 'mos.access.event'
    _description = 'MOS access event'
    _order = 'id desc'
    _log_access = False

    at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    user_id = fields.Many2one('res.users', readonly=True, ondelete='cascade')
    group_id = fields.Many2one('res.groups', readonly=True, ondelete='cascade')
    action = fields.Selection([('granted', 'Granted'), ('revoked', 'Revoked')], readonly=True)
    by_id = fields.Many2one('res.users', readonly=True)

    def write(self, vals):
        raise UserError(self.env._('The access history cannot be changed.'))

    def unlink(self):
        raise UserError(self.env._('The access history cannot be deleted.'))


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _mos_groups(self):
        privilege = self.env.ref('mos_core.res_groups_privilege_mos', raise_if_not_found=False)
        return self.sudo().group_ids.filtered(lambda g: g.privilege_id == privilege) if privilege else self.env['res.groups']

    def write(self, vals):
        tracked = any(k in vals for k in ('group_ids', 'groups_id'))
        before = {u.id: u._mos_groups() for u in self} if tracked else {}
        result = super().write(vals)
        if tracked:
            for user in self:
                after = user._mos_groups()
                for group in after - before[user.id]:
                    self.env['mos.access.event'].sudo().create({'user_id': user.id, 'group_id': group.id, 'action': 'granted', 'by_id': self.env.uid})
                for group in before[user.id] - after:
                    self.env['mos.access.event'].sudo().create({'user_id': user.id, 'group_id': group.id, 'action': 'revoked', 'by_id': self.env.uid})
        return result

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        for user in users:
            for group in user._mos_groups():
                self.env['mos.access.event'].sudo().create({'user_id': user.id, 'group_id': group.id, 'action': 'granted', 'by_id': self.env.uid})
        return users


class MosAccessReview(models.TransientModel):
    """A list of every user with their MOS roles, sites, last grant and who granted it, for the periodic access review (FR-PLT-017)."""

    _name = 'mos.access.review'
    _description = 'MOS access review'

    include_inactive = fields.Boolean()
    file = fields.Binary(readonly=True)
    filename = fields.Char(readonly=True)
    line_ids = fields.One2many('mos.access.review.line', 'review_id')

    def action_build(self):
        self.ensure_one()
        user = self.env.user
        if not (user.has_group('mos_core.group_mos_admin') or user.has_group('mos_approvals.group_mos_general_manager')):
            raise UserError(self.env._('Only an administrator or a general manager can run an access review.'))
        domain = [] if self.include_inactive else [('active', '=', True)]
        users = self.env['res.users'].sudo().with_context(active_test=not self.include_inactive).search(domain + [('share', '=', False)], order='name')
        rows = []
        for u in users:
            groups = u._mos_groups()
            if not groups:
                continue
            sites = ', '.join(u.mos_site_ids.mapped('code'))
            for group in groups:
                event = self.env['mos.access.event'].sudo().search([('user_id', '=', u.id), ('group_id', '=', group.id), ('action', '=', 'granted')], order='id desc', limit=1)
                rows.append(
                    {
                        'review_id': self.id,
                        'user_id': u.id,
                        'login': u.login,
                        'group_id': group.id,
                        'sites': sites or self.env._('(no site: sees nothing)'),
                        'granted_at': event.at,
                        'granted_by_id': event.by_id.id,
                        'last_login': u.login_date,
                    }
                )
        self.line_ids.unlink()
        self.env['mos.access.review.line'].create(rows)
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(['User', 'Login', 'Role', 'Sites', 'Granted at', 'Granted by', 'Last sign-in'])
        for line in self.line_ids:
            writer.writerow([line.user_id.name, line.login, line.group_id.name, line.sites, line.granted_at or '', line.granted_by_id.name or '', line.last_login or ''])
        self.write({'file': base64.b64encode(buffer.getvalue().encode()).decode(), 'filename': f'mos-access-review-{fields.Date.context_today(self)}.csv'})
        self.env['mos.audit.event'].record('access_review_run', self, f'{len(self.line_ids)} role assignments reviewed')
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class MosAccessReviewLine(models.TransientModel):
    _name = 'mos.access.review.line'
    _description = 'MOS access review line'
    _order = 'user_id, group_id'

    review_id = fields.Many2one('mos.access.review', required=True, ondelete='cascade')
    user_id = fields.Many2one('res.users')
    login = fields.Char()
    group_id = fields.Many2one('res.groups', string='Role')
    sites = fields.Char()
    granted_at = fields.Datetime()
    granted_by_id = fields.Many2one('res.users')
    last_login = fields.Datetime()
