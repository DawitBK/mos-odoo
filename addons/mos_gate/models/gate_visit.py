from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

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
        return super().create(vals_list)

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
            visit.write({'state': 'arrived', 'arrived_at': fields.Datetime.now()})

    def action_start_weighing(self):
        for visit in self:
            if visit.state != 'arrived':
                raise UserError(self.env._('Only an arrived vehicle can start weighing.'))
            self.env['mos.weigh.ticket'].create({'visit_id': visit.id})
            visit.state = 'weighing'

    def action_clear_exit(self):
        """Exit clearance: the ticket must be posted and any inbound decision made (FR-GATE-016)."""
        for visit in self:
            problems = visit._exit_blockers()
            if problems:
                raise UserError(self.env._('Exit refused: %s', '; '.join(problems)))
            visit.state = 'cleared'
            self.env['mos.audit.event'].record('exit_cleared', visit, visit.name)

    def action_exit(self):
        for visit in self:
            if visit.state != 'cleared':
                raise UserError(self.env._('Only an exit-cleared vehicle can leave.'))
            visit.write({'state': 'exited', 'exited_at': fields.Datetime.now()})

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
