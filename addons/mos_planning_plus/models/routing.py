from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosRouting(models.Model):
    """The steps a flour goes through: each with its work centre, setup time and run rate. Versioned and approved like a recipe;
    an approved version never changes, a change is a new version (FR-MFG-002)."""

    _name = 'mos.routing'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS routing'
    _order = 'product_id, version desc'
    _mos_final_states = ('approved', 'retired')
    _mos_editable_after_post = {'state', 'approved_by_id'}

    name = fields.Char(compute='_compute_name', store=True)
    product_id = fields.Many2one('product.product', required=True, string='Flour')
    version = fields.Integer(default=0, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('retired', 'Retired')], default='draft', required=True, readonly=True, tracking=True)
    op_ids = fields.One2many('mos.routing.op', 'routing_id', string='Operations')
    note = fields.Text()
    approved_by_id = fields.Many2one('res.users', readonly=True)

    _version_uniq = models.Constraint('unique(product_id, version)', 'This routing version already exists.')

    @api.depends('product_id', 'version')
    def _compute_name(self):
        for routing in self:
            routing.name = f'{routing.product_id.display_name or "?"} routing v{routing.version}'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('version'):
                top = self.search([('product_id', '=', vals['product_id'])], order='version desc', limit=1)
                vals['version'] = top.version + 1
        return super().create(vals_list)

    def action_approve(self):
        user = self.env.user
        if not (user.has_group('mos_manufacturing.group_mos_production_supervisor') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a production supervisor can approve a routing.'))
        for routing in self:
            if routing.state != 'draft':
                raise UserError(self.env._('Only a draft routing can be approved.'))
            if not routing.op_ids:
                raise UserError(self.env._('A routing needs at least one operation.'))
            if routing.create_uid == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The author of a routing cannot approve it.'))
            self.search([('product_id', '=', routing.product_id.id), ('state', '=', 'approved'), ('id', '!=', routing.id)]).write({'state': 'retired'})
            routing.write({'state': 'approved', 'approved_by_id': user.id})
            self.env['mos.audit.event'].record('routing_approved', routing, routing.name)

    def action_new_version(self):
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(self.env._('A draft is edited in place.'))
        return self.create(
            {
                'product_id': self.product_id.id,
                'op_ids': [Command.create({'sequence': o.sequence, 'name': o.name, 'work_centre_id': o.work_centre_id.id, 'setup_minutes': o.setup_minutes, 'run_rate_kg_h': o.run_rate_kg_h, 'reporting_point': o.reporting_point}) for o in self.op_ids],
            }
        )

    @api.model
    def in_force(self, product):
        return self.search([('product_id', '=', product.id), ('state', '=', 'approved')], limit=1)

    def minutes_for(self, kg):
        """(minutes, [(operation, minutes)]) to put `kg` through every step."""
        self.ensure_one()
        steps = []
        for op in self.op_ids.sorted('sequence'):
            run = kg / op.run_rate_kg_h * 60.0 if op.run_rate_kg_h else 0.0
            steps.append((op, round(op.setup_minutes + run, 1)))
        return round(sum(m for _op, m in steps), 1), steps


class MosRoutingOp(models.Model):
    _name = 'mos.routing.op'
    _description = 'MOS routing operation'
    _order = 'routing_id, sequence, id'

    routing_id = fields.Many2one('mos.routing', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    work_centre_id = fields.Many2one('mos.work.centre', required=True)
    setup_minutes = fields.Float(digits=(8, 1))
    run_rate_kg_h = fields.Float(string='Run rate (kg/hour)', required=True, digits=(12, 0))
    reporting_point = fields.Char(help='Where output or progress is reported for this step.')

    @api.constrains('setup_minutes', 'run_rate_kg_h')
    def _check_values(self):
        for op in self:
            if op.setup_minutes < 0 or op.run_rate_kg_h <= 0:
                raise ValidationError(self.env._('Setup cannot be negative and the run rate must be above zero.'))

    def _guard(self):
        if any(op.routing_id.state != 'draft' for op in self):
            raise UserError(self.env._('The operations of an approved routing cannot change; create a new version.'))

    @api.model_create_multi
    def create(self, vals_list):
        routings = self.env['mos.routing'].browse([v['routing_id'] for v in vals_list if v.get('routing_id')])
        if any(r.state != 'draft' for r in routings):
            raise UserError(self.env._('The operations of an approved routing cannot change; create a new version.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard()
        return super().write(vals)

    def unlink(self):
        self._guard()
        return super().unlink()


class MosWorkOrder(models.Model):
    _inherit = 'mos.work.order'

    routing_id = fields.Many2one('mos.routing', domain="[('product_id', '=', product_id), ('state', '=', 'approved')]")
    planned_minutes = fields.Float(compute='_compute_plan_time', string='Planned time (minutes)', digits=(12, 1))
    operation_sheet = fields.Text(compute='_compute_plan_time', string='Operations')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('routing_id') and vals.get('recipe_id'):
                product = self.env['mos.recipe'].browse(vals['recipe_id']).product_id
                routing = self.env['mos.routing'].in_force(product)
                if routing:
                    vals['routing_id'] = routing.id
        return super().create(vals_list)

    @api.depends('routing_id', 'routing_id.op_ids', 'planned_kg', 'work_centre_id')
    def _compute_plan_time(self):
        for order in self:
            if order.routing_id:
                total, steps = order.routing_id.minutes_for(order.planned_kg)
                order.planned_minutes = total
                order.operation_sheet = '\n'.join(f'{i}. {op.name} at {op.work_centre_id.display_name}: {minutes} min' + (f' (report at {op.reporting_point})' if op.reporting_point else '') for i, (op, minutes) in enumerate(steps, 1))
            elif order.work_centre_id.capacity_kg_h:
                order.planned_minutes = round(order.planned_kg / order.work_centre_id.capacity_kg_h * 60.0, 1)
                order.operation_sheet = False
            else:
                order.planned_minutes = 0.0
                order.operation_sheet = False
