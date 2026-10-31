from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MosDepartment(models.Model):
    """A department: a hierarchy with a head, the one a request or an order is raised for (FR-PLT-007).

    People belong to one home department (res.users.mos_department_id). Documents record the requesting department
    and, for purchase orders, the purchasing department.
    """

    _name = 'mos.department'
    _inherit = ['mos.audited']
    _description = 'MOS department'
    _parent_name = 'parent_id'
    _parent_store = True
    _rec_name = 'complete_name'
    _order = 'complete_name'

    name = fields.Char(required=True)
    code = fields.Char(size=12)
    parent_id = fields.Many2one('mos.department', string='Part of', index=True, ondelete='restrict')
    parent_path = fields.Char(index=True, unaccent=False)
    child_ids = fields.One2many('mos.department', 'parent_id', string='Sub-departments')
    complete_name = fields.Char(compute='_compute_complete_name', store=True, recursive=True)
    head_id = fields.Many2one('res.users', string='Head')
    member_ids = fields.One2many('res.users', 'mos_department_id', string='People')
    member_count = fields.Integer(compute='_compute_member_count')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('unique(complete_name, company_id)', 'A department of that name already exists under the same parent.')

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for dept in self:
            dept.complete_name = f'{dept.parent_id.complete_name} / {dept.name}' if dept.parent_id else dept.name

    def _compute_member_count(self):
        counts = dict(self.env['res.users']._read_group([('mos_department_id', 'in', self.ids)], ['mos_department_id'], ['__count']))
        for dept in self:
            dept.member_count = counts.get(dept, 0)

    @api.constrains('parent_id')
    def _check_no_loop(self):
        if self._has_cycle():
            raise ValidationError(self.env._('A department cannot be part of itself.'))

    def write(self, vals):
        # Refused before anything is written: the full name is worked out from the parent chain, which a loop would never finish.
        if vals.get('parent_id'):
            for dept in self:
                if vals['parent_id'] in self.search([('id', 'child_of', dept.id)]).ids:
                    raise ValidationError(self.env._('%s cannot be part of itself or of one of its own sub-departments.', dept.display_name))
        return super().write(vals)

    @api.constrains('active')
    def _check_deactivation(self):
        for dept in self.filtered(lambda d: not d.active):
            if dept.member_ids or dept.child_ids.filtered('active'):
                raise ValidationError(self.env._('%s still has people or active sub-departments; move them first.', dept.display_name))

    @api.model
    def by_name(self, name):
        """The department with this full or short name. Made when it does not exist, but only for an administrator or the system (seeds, imports): a text written by anyone else never adds to the master list."""
        name = (name or '').strip()
        if not name:
            return self.browse()
        found = self.search([('complete_name', '=ilike', name)], limit=1) or self.search([('name', '=ilike', name)], limit=1)
        if found or not (self.env.su or self.env.user.has_group('mos_core.group_mos_admin')):
            return found
        return self.create({'name': name})

    def action_open_members(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._('People in %s', self.name), 'res_model': 'res.users',
            'view_mode': 'list,form', 'domain': [('mos_department_id', '=', self.id)],
        }
