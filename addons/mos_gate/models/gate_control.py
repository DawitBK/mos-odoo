from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


def _norm(value):
    return (value or '').strip().upper()


class MosGateBlacklist(models.Model):
    """Vehicles, drivers and transporters the gate must stop or flag (FR-GATE-020, 021)."""

    _name = 'mos.gate.blacklist'
    _inherit = ['mos.audited']
    _description = 'MOS gate blacklist entry'
    _order = 'kind, value'

    kind = fields.Selection([('plate', 'Vehicle plate'), ('driver', 'Driver'), ('transporter', 'Transporter')], required=True, default='plate')
    value = fields.Char(required=True)
    severity = fields.Selection([('block', 'Block entry'), ('warn', 'Warn only')], required=True, default='block')
    reason = fields.Text(required=True)
    expires_on = fields.Date(help='The entry stops applying after this date. Empty means until removed.')
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('value'):
                vals['value'] = _norm(vals['value'])
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('value'):
            vals['value'] = _norm(vals['value'])
        return super().write(vals)

    @api.model
    def matches(self, plate=None, driver=None, transporter=None):
        """Active, unexpired entries that apply to this arrival."""
        today = fields.Date.context_today(self)
        wanted = [('plate', _norm(plate)), ('driver', _norm(driver)), ('transporter', _norm(transporter))]
        found = self.browse()
        for kind, value in wanted:
            if value:
                found |= self.sudo().search([('kind', '=', kind), ('value', '=', value)])
        return found.filtered(lambda e: not e.expires_on or e.expires_on >= today)


class MosGateShift(models.Model):
    """A guard's shift at a site. Gate actions by guards need an open shift when it is required (FR-GATE-001..003)."""

    _name = 'mos.gate.shift'
    _inherit = ['mos.audited']
    _description = 'MOS gate shift'
    _order = 'id desc'

    site_id = fields.Many2one('mos.site', required=True)
    guard_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user)
    started_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    ended_at = fields.Datetime(readonly=True)
    state = fields.Selection([('open', 'Open'), ('closed', 'Closed')], default='open', required=True, readonly=True)
    handover_notes = fields.Text()
    visit_count = fields.Integer(compute='_compute_visit_count')

    def _compute_visit_count(self):
        for shift in self:
            shift.visit_count = self.env['mos.gate.visit'].sudo().search_count([('shift_id', '=', shift.id)])

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            guard = vals.get('guard_id') or self.env.uid
            if self.sudo().search_count([('guard_id', '=', guard), ('state', '=', 'open')]):
                raise ValidationError(self.env._('This guard already has an open shift. Close it first.'))
        return super().create(vals_list)

    def action_close(self):
        for shift in self:
            if shift.state == 'closed':
                raise UserError(self.env._('The shift is already closed.'))
            open_visits = self.env['mos.gate.visit'].sudo().search_count([('shift_id', '=', shift.id), ('state', 'in', ('arrived', 'weighing', 'weighed', 'cleared'))])
            if open_visits and not shift.handover_notes:
                raise UserError(self.env._('%s vehicles are still on site: write handover notes before closing the shift.', open_visits))
            shift.write({'state': 'closed', 'ended_at': fields.Datetime.now()})

    @api.model
    def current_for(self, site, user=None):
        user = user or self.env.user
        return self.sudo().search([('guard_id', '=', user.id), ('site_id', '=', site.id), ('state', '=', 'open')], limit=1)


class MosGateVehicle(models.Model):
    """Known vehicle: stored tare weight and the legal loading limit (FR-GATE-012, 013)."""

    _name = 'mos.gate.vehicle'
    _inherit = ['mos.audited']
    _description = 'MOS gate vehicle'
    _order = 'plate'
    _rec_name = 'plate'

    plate = fields.Char(required=True)
    transporter = fields.Char()
    stored_tare = fields.Float(string='Stored tare (kg)', digits=(12, 1))
    tare_at = fields.Datetime(string='Tare weighed at', readonly=True)
    tare_valid = fields.Boolean(compute='_compute_tare_valid')
    max_gross_kg = fields.Float(string='Maximum gross (kg)', digits=(12, 1), help='Zero means no limit is set.')
    active = fields.Boolean(default=True)

    _plate_uniq = models.Constraint('unique(plate)', 'This vehicle is already registered.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['plate'] = _norm(vals.get('plate'))
            if vals.get('stored_tare') and not vals.get('tare_at'):
                vals['tare_at'] = fields.Datetime.now()
        return super().create(vals_list)

    @api.depends('stored_tare', 'tare_at')
    def _compute_tare_valid(self):
        days = int(self.env['ir.config_parameter'].sudo().get_str('mos_gate.tare_valid_days', '30') or 30)
        limit = fields.Datetime.now() - timedelta(days=days)
        for vehicle in self:
            vehicle.tare_valid = bool(vehicle.stored_tare and vehicle.tare_at and vehicle.tare_at >= limit)

    @api.model
    def for_plate(self, plate, create=False):
        plate = _norm(plate)
        vehicle = self.sudo().with_context(active_test=False).search([('plate', '=', plate)], limit=1)
        if not vehicle and create:
            vehicle = self.sudo().create({'plate': plate})
        return vehicle


class MosGateIncident(models.Model):
    """Something that went wrong at the gate: overload, tare deviation, rejection, safety (FR-GATE-025)."""

    _name = 'mos.gate.incident'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS gate incident'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    visit_id = fields.Many2one('mos.gate.visit', ondelete='restrict')
    kind = fields.Selection(
        [
            ('overload', 'Overload'),
            ('tare_deviation', 'Tare deviation'),
            ('rejection', 'Load rejected'),
            ('safety', 'Safety'),
            ('theft_suspicion', 'Suspected theft'),
            ('other', 'Other'),
        ],
        required=True,
        default='other',
        tracking=True,
    )
    severity = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='medium', required=True, tracking=True)
    description = fields.Text(required=True)
    reported_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([('open', 'Open'), ('investigating', 'Investigating'), ('closed', 'Closed')], default='open', required=True, tracking=True, readonly=True)
    resolution = fields.Text()
    closed_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.gate.incident')
        return super().create(vals_list)

    def action_investigate(self):
        self.filtered(lambda i: i.state == 'open').write({'state': 'investigating'})

    def action_close(self):
        for incident in self:
            if not incident.resolution:
                raise UserError(self.env._('Write the resolution before closing the incident.'))
            incident.write({'state': 'closed', 'closed_at': fields.Datetime.now()})


class MosGatePass(models.Model):
    """Material gate pass: goods leaving or entering the site outside a weighed visit (FR-GATE-030..034).

    Requested by one person, approved by another. A returnable pass stays open until the goods come back.
    """

    _name = 'mos.gate.pass'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS material gate pass'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    kind = fields.Selection([('returnable', 'Returnable: goods come back'), ('final', 'Final: goods leave for good'), ('inbound', 'Inbound: goods arriving')], required=True, default='final')
    carrier = fields.Char(string='Carried by', required=True)
    destination = fields.Char()
    purpose = fields.Text(required=True)
    line_ids = fields.One2many('mos.gate.pass.line', 'pass_id', string='Items')
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('approved', 'Approved'), ('out', 'Out'), ('returned', 'Returned'), ('closed', 'Closed'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        tracking=True,
        readonly=True,
    )
    expected_return = fields.Date()
    out_at = fields.Datetime(readonly=True)
    returned_at = fields.Datetime(readonly=True)
    overdue = fields.Boolean(compute='_compute_overdue', search='_search_overdue')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.gate.pass')
        return super().create(vals_list)

    @api.depends('state', 'kind', 'expected_return')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for gp in self:
            gp.overdue = gp.kind == 'returnable' and gp.state == 'out' and bool(gp.expected_return) and gp.expected_return < today

    def _search_overdue(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [('kind', '=', 'returnable'), ('state', '=', 'out'), ('expected_return', '<', today)]
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(self.env._('Unsupported search on overdue.'))
        values = [value] if isinstance(value, bool) else list(value)
        positive = (operator in ('=', 'in')) == (True in values)
        return domain if positive else ['!', '&', '&'] + domain

    @api.constrains('kind', 'expected_return')
    def _check_return_date(self):
        for gp in self:
            if gp.kind == 'returnable' and not gp.expected_return:
                raise ValidationError(self.env._('A returnable pass needs an expected return date.'))

    def action_approve(self):
        user = self.env.user
        if not (user.has_group('mos_gate.group_mos_gate_pass_approver') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a gate pass approver can approve a pass.'))
        for gp in self:
            if gp.state != 'draft':
                raise UserError(self.env._('Only a draft pass can be approved.'))
            if not gp.line_ids:
                raise UserError(self.env._('List at least one item.'))
            if gp.requested_by_id == self.env.user:
                raise UserError(self.env._('You cannot approve a pass you requested yourself.'))
            gp.write({'state': 'approved', 'approved_by_id': self.env.uid})

    def action_out(self):
        for gp in self:
            if gp.state != 'approved':
                raise UserError(self.env._('The pass must be approved before the goods leave.'))
            gp.write({'state': 'out', 'out_at': fields.Datetime.now()})
            self.env['mos.audit.event'].record('gate_pass_out', gp, gp.name)

    def action_return(self):
        for gp in self:
            if gp.state != 'out' or gp.kind != 'returnable':
                raise UserError(self.env._('Only goods out on a returnable pass can be returned.'))
            gp.write({'state': 'returned', 'returned_at': fields.Datetime.now()})

    def action_close(self):
        for gp in self:
            if gp.state not in ('out', 'returned') or (gp.state == 'out' and gp.kind == 'returnable'):
                raise UserError(self.env._('A returnable pass is closed after the goods return; a final or inbound pass after the goods move.'))
            gp.state = 'closed'

    def action_cancel(self):
        for gp in self:
            if gp.state in ('out', 'returned', 'closed', 'cancelled'):
                raise UserError(self.env._('This pass can no longer be cancelled.'))
            gp.state = 'cancelled'


class MosGatePassLine(models.Model):
    _name = 'mos.gate.pass.line'
    _description = 'MOS material gate pass line'

    pass_id = fields.Many2one('mos.gate.pass', required=True, ondelete='cascade')
    name = fields.Char(string='Item', required=True)
    quantity = fields.Float(default=1.0, required=True)
    unit = fields.Char(default='pcs')

    @api.constrains('quantity')
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(self.env._('Item quantities must be above zero.'))
