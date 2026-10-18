import secrets

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import BinaryBytes

OPEN_STATES = ('arrived', 'weighing', 'weighed', 'cleared')


class MosGateVisit(models.Model):
    """One truck's visit to the site: arrival, weighing, exit clearance (FR-GATE-005..018)."""

    _name = 'mos.gate.visit'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS gate visit'
    _order = 'id desc'

    name = fields.Char(default='New', readonly=True, copy=False)
    site_id = fields.Many2one('mos.site', required=True, tracking=True)
    purpose = fields.Selection([('inbound_po', 'Inbound: PO delivery'), ('outbound_do', 'Outbound: customer delivery')], default='inbound_po', required=True)
    plate = fields.Char(required=True, tracking=True)
    driver_name = fields.Char()
    transporter = fields.Char()
    partner_id = fields.Many2one('res.partner', string='Supplier', domain="[('supplier_rank', '>', 0)]")
    purchase_id = fields.Many2one('purchase.order', string='Purchase order')
    state = fields.Selection(
        [
            ('expected', 'Expected'),
            ('arrived', 'Arrived'),
            ('weighing', 'Weighing'),
            ('weighed', 'Weighed'),
            ('cleared', 'Exit cleared'),
            ('exited', 'Exited'),
            ('cancelled', 'Cancelled'),
        ],
        default='arrived',
        required=True,
        tracking=True,
        readonly=True,
    )
    ticket_ids = fields.One2many('mos.weigh.ticket', 'visit_id', string='Weigh tickets')
    ticket_id = fields.Many2one('mos.weigh.ticket', string='Current ticket', compute='_compute_ticket_id', store=True)
    net_weight = fields.Float(related='ticket_id.net_weight', string='Net weight (kg)')
    arrived_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    exited_at = fields.Datetime(readonly=True)
    shift_id = fields.Many2one('mos.gate.shift', readonly=True, ondelete='restrict')
    blacklist_warning = fields.Text(readonly=True)
    exit_code = fields.Char(string='Exit pass code', readonly=True, copy=False)
    exit_qr = fields.Binary(string='Exit pass QR', compute='_compute_exit_qr')
    incident_ids = fields.One2many('mos.gate.incident', 'visit_id')
    incident_count = fields.Integer(compute='_compute_incident_count')
    vehicle_id = fields.Many2one('mos.gate.vehicle', compute='_compute_vehicle')

    @api.depends('plate')
    def _compute_vehicle(self):
        for visit in self:
            visit.vehicle_id = self.env['mos.gate.vehicle'].sudo().for_plate(visit.plate) if visit.plate else False

    @api.depends('incident_ids')
    def _compute_incident_count(self):
        for visit in self:
            visit.incident_count = len(visit.incident_ids)

    @api.depends('exit_code')
    def _compute_exit_qr(self):
        report = self.env['ir.actions.report']
        for visit in self:
            visit.exit_qr = BinaryBytes(report.barcode('QR', visit.exit_code, width=240, height=240)) if visit.exit_code else False

    @api.depends('ticket_ids.state')
    def _compute_ticket_id(self):
        for visit in self:
            live = visit.ticket_ids.filtered(lambda t: t.state != 'superseded')
            visit.ticket_id = live[-1:] if live else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.gate.visit')
            if vals.get('plate'):
                vals['plate'] = vals['plate'].strip().upper()
            site = self.env['mos.site'].browse(vals.get('site_id'))
            self._require_shift(site)
            hits = self.env['mos.gate.blacklist'].matches(vals.get('plate'), vals.get('driver_name'), vals.get('transporter'))
            blocking = hits.filtered(lambda e: e.severity == 'block')
            if blocking:
                raise UserError(self.env._('Entry refused: %s', '; '.join(f'{e.kind} {e.value}: {e.reason}' for e in blocking)))
            if hits:
                vals['blacklist_warning'] = '; '.join(f'{e.kind} {e.value}: {e.reason}' for e in hits)
            shift = self.env['mos.gate.shift'].current_for(site) if site else False
            if shift:
                vals['shift_id'] = shift.id
        return super().create(vals_list)

    @api.model
    def _require_shift(self, site):
        """Guards act only inside an open shift at the site when the policy asks for it (FR-GATE-002)."""
        if not self.env['ir.config_parameter'].sudo().get_bool('mos_gate.require_shift'):
            return
        user = self.env.user
        if user.has_group('mos_core.group_mos_admin') or not user.has_group('mos_gate.group_mos_gate_guard'):
            return
        if not site or not self.env['mos.gate.shift'].current_for(site):
            raise UserError(self.env._('Open your shift at this site before working the gate.'))

    @api.constrains('plate', 'state')
    def _check_one_open_visit(self):
        """One open visit per vehicle at a time (FR-GATE-008)."""
        for visit in self.filtered(lambda v: v.state in OPEN_STATES):
            clash = self.search_count([('plate', '=', visit.plate), ('state', 'in', OPEN_STATES), ('id', '!=', visit.id)])
            if clash:
                raise ValidationError(self.env._('Vehicle %s already has an open visit.', visit.plate))

    def action_arrive(self):
        """An expected truck reaches the gate (FR-GATE-004, 005)."""
        for visit in self:
            if visit.state != 'expected':
                raise UserError(self.env._('Only an expected vehicle can be marked as arrived.'))
            self._require_shift(visit.site_id)
            if self.env['mos.gate.blacklist'].matches(visit.plate, visit.driver_name, visit.transporter).filtered(lambda e: e.severity == 'block'):
                raise UserError(self.env._('Entry refused: this vehicle, driver or transporter is blocked.'))
            visit.write({'state': 'arrived', 'arrived_at': fields.Datetime.now()})

    def action_start_weighing(self):
        for visit in self:
            if visit.state != 'arrived':
                raise UserError(self.env._('Only an arrived vehicle can start weighing.'))
            self._require_shift(visit.site_id)
            self.env['mos.weigh.ticket'].create({'visit_id': visit.id})
            visit.state = 'weighing'

    def action_clear_exit(self):
        """Exit clearance: the ticket must be posted and any inbound decision made (FR-GATE-016)."""
        for visit in self:
            problems = visit._exit_blockers()
            if problems:
                raise UserError(self.env._('Exit refused: %s', '; '.join(problems)))
            visit.write({'state': 'cleared', 'exit_code': secrets.token_hex(5).upper()})
            self.env['mos.audit.event'].record('exit_cleared', visit, visit.name)

    def action_exit(self, code=None):
        """The vehicle leaves. When the policy asks for it the exit pass code must be presented (FR-GATE-017)."""
        require = self.env['ir.config_parameter'].sudo().get_bool('mos_gate.require_exit_code')
        for visit in self:
            if visit.state != 'cleared':
                raise UserError(self.env._('Only an exit-cleared vehicle can leave.'))
            self._require_shift(visit.site_id)
            if (require or code) and (code or '').strip().upper() != visit.exit_code:
                raise UserError(self.env._('The exit pass code does not match this vehicle.'))
            visit.write({'state': 'exited', 'exited_at': fields.Datetime.now()})

    @api.model
    def scan_exit(self, code):
        """Guard scans the QR on the exit pass: finds the cleared visit and lets it leave."""
        visit = self.search([('exit_code', '=', (code or '').strip().upper()), ('state', '=', 'cleared')], limit=1)
        if not visit:
            raise UserError(self.env._('No vehicle is waiting to leave on this exit pass.'))
        visit.action_exit(code=code)
        return visit

    def action_view_incidents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Incidents'),
            'res_model': 'mos.gate.incident',
            'view_mode': 'list,form',
            'domain': [('visit_id', '=', self.id)],
            'context': {'default_visit_id': self.id, 'default_site_id': self.site_id.id},
        }

    def action_cancel(self):
        for visit in self:
            if visit.state in ('exited', 'cancelled'):
                raise UserError(self.env._('This visit is already closed.'))
            if visit.ticket_id and visit.ticket_id.state == 'posted':
                raise UserError(self.env._('A visit with a posted weigh ticket cannot be cancelled.'))
            visit.state = 'cancelled'

    def _exit_blockers(self):
        """Hook for modules that add clearance rules (lab decision, open holds)."""
        self.ensure_one()
        problems = []
        if self.state != 'weighed':
            problems.append(self.env._('the vehicle has not completed weighing'))
        elif not self.ticket_id or self.ticket_id.state != 'posted':
            problems.append(self.env._('the weigh ticket is not posted'))
        return problems
