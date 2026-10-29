from odoo import api, fields, models
from odoo.exceptions import UserError


class MosGateRegister(models.TransientModel):
    """The daily gate register and the weigh ticket register, with voided and superseded tickets listed too (FR-GATE-025)."""

    _name = 'mos.gate.register'
    _description = 'MOS gate register'

    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user._mos_current_site())
    date = fields.Date(required=True, default=fields.Date.context_today)
    visit_ids = fields.Many2many('mos.gate.visit', compute='_compute_rows')
    ticket_ids = fields.Many2many('mos.weigh.ticket', compute='_compute_rows')
    visits = fields.Integer(compute='_compute_rows')
    net_total = fields.Float(compute='_compute_rows', string='Net weighed (kg)')
    superseded = fields.Integer(compute='_compute_rows')

    @api.depends('site_id', 'date')
    def _compute_rows(self):
        for reg in self:
            day = fields.Datetime.to_datetime(reg.date)
            end = day.replace(hour=23, minute=59, second=59)
            visits = self.env['mos.gate.visit'].search([('site_id', '=', reg.site_id.id), ('arrived_at', '>=', day), ('arrived_at', '<=', end)], order='arrived_at') if reg.site_id and reg.date else self.env['mos.gate.visit']
            tickets = visits.ticket_ids
            reg.visit_ids = visits
            reg.ticket_ids = tickets
            reg.visits = len(visits)
            reg.net_total = sum(tickets.filtered(lambda t: t.state == 'posted').mapped('net_weight'))
            reg.superseded = len(tickets.filtered(lambda t: t.state == 'superseded'))

    def action_print(self):
        self.ensure_one()
        return self.env.ref('mos_gate_plus.action_report_gate_register').report_action(self)


class MosGateArrival(models.TransientModel):
    """The guard types or scans a plate; the system finds the expected visit or opens a walk-in (FR-GATE-005)."""

    _name = 'mos.gate.arrival'
    _description = 'MOS gate arrival'

    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user._mos_current_site())
    gate_post_id = fields.Many2one('mos.gate.post', domain="[('site_id', '=', site_id)]")
    plate = fields.Char(required=True)
    purpose = fields.Selection(
        [('inbound_po', 'Inbound: PO delivery'), ('outbound_do', 'Outbound: customer delivery'), ('inbound_transfer', 'Inbound transfer'), ('outbound_transfer', 'Outbound transfer'), ('service', 'Service or contractor'), ('other', 'Other')],
        default='inbound_po',
    )
    driver_name = fields.Char()
    reference_doc = fields.Char(string='Document brought')

    def action_arrive(self):
        self.ensure_one()
        visit = self.env['mos.gate.visit'].arrive_by_plate(
            self.plate,
            self.site_id,
            purpose=self.purpose,
            driver_name=self.driver_name,
            reference_doc=self.reference_doc,
            gate_post_id=self.gate_post_id.id or False,
        )
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.gate.visit', 'res_id': visit.id, 'view_mode': 'form', 'target': 'current'}


class MosGateActionWizard(models.TransientModel):
    """Reason dialog for the gate acts that need a reason."""

    _name = 'mos.gate.action.wizard'
    _description = 'MOS gate action with a reason'

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    method = fields.Char(required=True, readonly=True)
    reason = fields.Char(required=True)

    ALLOWED = {
        ('mos.gate.visit', 'action_override_clearance'),
        ('mos.gate.visit', 'action_approve_tolerance'),
        ('mos.paper.slip', 'action_void'),
    }

    def action_confirm(self):
        self.ensure_one()
        if (self.res_model, self.method) not in self.ALLOWED:
            raise UserError(self.env._('That action is not available here.'))
        getattr(self.env[self.res_model].browse(self.res_id), self.method)(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class MosGateOpener(models.AbstractModel):
    _name = 'mos.gate.opener'
    _description = 'Opens the gate reason dialog'

    def action_gate_ui(self):
        self.ensure_one()
        method = self.env.context.get('mos_method')
        if (self._name, method) not in MosGateActionWizard.ALLOWED:
            raise UserError(self.env._('That action is not available here.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env.context.get('mos_title') or method,
            'res_model': 'mos.gate.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_method': method},
        }


class MosGateVisitUi(models.Model):
    _name = 'mos.gate.visit'
    _inherit = ['mos.gate.visit', 'mos.gate.opener']


class MosPaperSlipUi(models.Model):
    _name = 'mos.paper.slip'
    _inherit = ['mos.paper.slip', 'mos.gate.opener']
