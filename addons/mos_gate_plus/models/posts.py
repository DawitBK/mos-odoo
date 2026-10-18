from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosGatePost(models.Model):
    """A gate: which way traffic moves through it and which kinds of visit it accepts (FR-GATE-001)."""

    _name = 'mos.gate.post'
    _inherit = ['mos.audited']
    _description = 'MOS gate post'
    _order = 'code'

    code = fields.Char(required=True, size=12)
    name = fields.Char(required=True)
    site_id = fields.Many2one('mos.site', required=True)
    direction = fields.Selection([('in', 'Entry'), ('out', 'Exit'), ('both', 'Entry and exit')], default='both', required=True)
    allows = fields.Selection([('all', 'Every kind of visit'), ('goods', 'Goods only (no service visits)'), ('service', 'Service and contractor visits only')], default='all', required=True)
    weighbridge_ids = fields.One2many('mos.weighbridge', 'post_id')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(code, site_id)', 'The gate code must be unique per site.')


class MosWeighbridge(models.Model):
    """A scale: its capacity and smallest division, and its own gap-free ticket numbers (FR-GATE-001, 030)."""

    _name = 'mos.weighbridge'
    _inherit = ['mos.audited']
    _description = 'MOS weighbridge'
    _order = 'code'

    code = fields.Char(required=True, size=12)
    name = fields.Char(required=True)
    post_id = fields.Many2one('mos.gate.post', required=True)
    site_id = fields.Many2one(related='post_id.site_id', store=True)
    capacity_kg = fields.Float(string='Capacity (kg)', required=True, default=80000.0, digits=(12, 0))
    division_kg = fields.Float(string='Division (kg)', required=True, default=10.0, digits=(8, 3))
    sequence_id = fields.Many2one('ir.sequence', readonly=True, copy=False, ondelete='restrict')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(code, post_id)', 'The weighbridge code must be unique per gate.')

    @api.constrains('capacity_kg', 'division_kg')
    def _check(self):
        for bridge in self:
            if bridge.capacity_kg <= 0 or bridge.division_kg <= 0:
                raise ValidationError(self.env._('Capacity and division must be above zero.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for bridge in records:
            bridge.sequence_id = self.env['ir.sequence'].sudo().create(
                {
                    'name': f'MOS weigh ticket {bridge.code}',
                    'code': f'mos.weigh.ticket.wb{bridge.id}',
                    'prefix': f'WT-{bridge.code}-%(range_year)s-',
                    'padding': 6,
                    'implementation': 'no_gap',
                    'use_date_range': True,
                }
            )
        return records

    def check_weight(self, weight):
        """A weight the scale could not have shown is refused."""
        self.ensure_one()
        if weight > self.capacity_kg + 0.0001:
            raise UserError(self.env._('%(weight)s kg is above the capacity of %(bridge)s (%(cap)s kg).', weight=weight, bridge=self.name, cap=self.capacity_kg))
        steps = weight / self.division_kg
        if abs(steps - round(steps)) > 1e-6:
            raise UserError(self.env._('%(weight)s kg is not a multiple of the scale division (%(div)s kg).', weight=weight, div=self.division_kg))


class MosGateVisit(models.Model):
    """More about the vehicle and the visit, and more kinds of visit (FR-GATE-006, 007, 020)."""

    _inherit = 'mos.gate.visit'

    purpose = fields.Selection(
        selection_add=[('inbound_transfer', 'Inbound: transfer from another site'), ('outbound_transfer', 'Outbound: transfer to another site'), ('service', 'Service or contractor visit'), ('other', 'Other')],
        ondelete={'inbound_transfer': 'set default', 'outbound_transfer': 'set default', 'service': 'set default', 'other': 'set default'},
    )
    gate_post_id = fields.Many2one('mos.gate.post')
    weighbridge_id = fields.Many2one('mos.weighbridge', domain="[('post_id', '=', gate_post_id)]")
    trailer_plate = fields.Char()
    driver_license = fields.Char(string="Driver's licence or ID")
    driver_phone = fields.Char()
    reference_doc = fields.Char(string='Document brought (delivery note, permit)')
    seal_number = fields.Char()
    seal_intact = fields.Boolean(default=True)
    persons_count = fields.Integer(string='People entering')
    work_permit_ref = fields.Char(string='Work permit')
    tool_ids = fields.One2many('mos.gate.visit.tool', 'visit_id', string='Tools and equipment')
    expected_auto = fields.Boolean(readonly=True, help='Created from a purchase order delivery date.')
    clearance_override_by_id = fields.Many2one('res.users', readonly=True)
    clearance_override_reason = fields.Char(readonly=True)
    from_paper = fields.Boolean(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('trailer_plate'):
                vals['trailer_plate'] = vals['trailer_plate'].strip().upper()
            post = self.env['mos.gate.post'].browse(vals.get('gate_post_id'))
            if post:
                purpose = vals.get('purpose', 'inbound_po')
                if post.allows == 'goods' and purpose == 'service':
                    raise UserError(self.env._('Gate %s does not take service visits.', post.name))
                if post.allows == 'service' and purpose != 'service':
                    raise UserError(self.env._('Gate %s takes service and contractor visits only.', post.name))
                if post.direction == 'out' and purpose.startswith('inbound'):
                    raise UserError(self.env._('Gate %s is an exit: inbound vehicles enter elsewhere.', post.name))
                if post.direction == 'in' and purpose.startswith('outbound'):
                    raise UserError(self.env._('Gate %s is an entry: outbound vehicles are cleared at the exit.', post.name))
        return super().create(vals_list)

    def _mos_requires_weighing(self):
        if self.purpose == 'service':
            return False
        return super()._mos_requires_weighing()

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        if self.purpose == 'service':
            open_tools = self.tool_ids.filtered(lambda t: t.qty_out < t.qty_in and not t.explanation)
            if open_tools:
                problems.append(self.env._('tools not taken out again: %s', ', '.join(open_tools.mapped('name'))))
        elif self.env['ir.config_parameter'].sudo().get_bool('mos_gate_plus.require_seal') and not self.seal_intact and not self.clearance_override_by_id:
            problems.append(self.env._('the seal is broken or missing (a gate supervisor can override with a reason)'))
        return problems

    def action_override_clearance(self, reason):
        """A gate supervisor lets a vehicle go despite an overridable check (FR-GATE-017)."""
        user = self.env.user
        if not (user.has_group('mos_flows.group_mos_gate_supervisor') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a gate supervisor can override a clearance check.'))
        if not reason:
            raise UserError(self.env._('Give the reason for the override.'))
        for visit in self:
            visit.sudo().write({'clearance_override_by_id': user.id, 'clearance_override_reason': reason})
            self.env['mos.audit.event'].record('clearance_overridden', visit, f'{visit.name}: {reason}')
            self.env['mos.gate.incident'].sudo().create({'site_id': visit.site_id.id, 'visit_id': visit.id, 'kind': 'other', 'severity': 'medium', 'description': self.env._('Clearance override: %s', reason)})

    # -- plate search at the gate (FR-GATE-005) --------------------------------------------------------------------
    @api.model
    def arrive_by_plate(self, plate, site, **defaults):
        """The guard types or scans a plate: the matching expected visit arrives, or a walk-in visit is opened."""
        plate = (plate or '').strip().upper()
        if not plate:
            raise UserError(self.env._('Enter the plate number.'))
        expected = self.sudo().search([('state', '=', 'expected'), ('site_id', '=', site.id), '|', ('plate', '=', plate), ('purchase_id.name', 'ilike', plate)])
        if len(expected) > 1:
            raise UserError(self.env._('%(n)s expected visits match "%(plate)s": open the right one from the yard board.', n=len(expected), plate=plate))
        if expected:
            visit = expected.with_env(self.env)
            if visit.plate.startswith('TBA-'):
                visit.write({'plate': plate})
            visit.action_arrive()
            return visit
        return self.create({'site_id': site.id, 'plate': plate, **defaults})

    @api.model
    def _cron_expected_arrivals(self):
        """Purchase orders due to deliver soon get an expected visit, so the gate sees what is coming (FR-GATE-004)."""
        days = self.env['ir.config_parameter'].sudo().get_int('mos_gate_plus.expected_days_ahead', 1)
        horizon = fields.Datetime.now() + timedelta(days=days)
        created = 0
        lines = self.env['purchase.order.line'].sudo().search([('order_id.state', '=', 'purchase'), ('date_planned', '<=', horizon)])
        for order in lines.filtered(lambda l: l.qty_received < l.product_qty).order_id:
            site = self.env['mos.site'].sudo().search([('warehouse_id', '=', order.picking_type_id.warehouse_id.id)], limit=1)
            if not site or self.sudo().search_count([('purchase_id', '=', order.id), ('state', 'in', ('expected', 'arrived', 'weighing', 'weighed', 'cleared'))]):
                continue
            self.sudo().create(
                {'site_id': site.id, 'plate': f'TBA-{order.name}', 'purpose': 'inbound_po', 'purchase_id': order.id, 'partner_id': order.partner_id.id, 'state': 'expected', 'expected_auto': True}
            )
            created += 1
        return created


class MosGateVisitTool(models.Model):
    """Tools a contractor brought in and what left again; exit is held until they match (FR-GATE-020)."""

    _name = 'mos.gate.visit.tool'
    _description = 'MOS visit tool'

    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    qty_in = fields.Integer(default=1)
    qty_out = fields.Integer()
    explanation = fields.Char(help='Why fewer left than came in (consumed, left on site by agreement).')

    @api.constrains('qty_in', 'qty_out')
    def _check(self):
        for tool in self:
            if tool.qty_in < 0 or tool.qty_out < 0 or tool.qty_out > tool.qty_in:
                raise ValidationError(self.env._('Quantities cannot be negative, and more cannot leave than came in.'))


class MosWeighTicket(models.Model):
    """Tickets take their number from their weighbridge, and the weights are checked against the scale (FR-GATE-001, 030)."""

    _inherit = 'mos.weigh.ticket'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New' and vals.get('visit_id'):
                bridge = self.env['mos.gate.visit'].browse(vals['visit_id']).weighbridge_id
                if bridge and bridge.sequence_id:
                    vals['name'] = bridge.sequence_id.next_by_id()
        return super().create(vals_list)

    def _check_scale(self, weight):
        bridge = self.visit_id.weighbridge_id
        if bridge:
            bridge.check_weight(weight)

    def record_first(self, weight):
        self._check_scale(weight)
        return super().record_first(weight)

    def record_second(self, weight):
        self._check_scale(weight)
        return super().record_second(weight)
