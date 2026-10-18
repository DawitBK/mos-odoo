from odoo import fields, models
from odoo.exceptions import AccessError


class MosOverview(models.TransientModel):
    """The overview gains a panel for each role: what is waiting for this person, counted live (FR-RPT-001).

    A figure the person has no right to see reads as zero rather than failing the whole page.
    """

    _inherit = 'mos.overview'

    exceptions_mine = fields.Integer(compute='_compute_role_figures', string='Exceptions assigned to me')
    exceptions_open = fields.Integer(compute='_compute_role_figures', string='Open exceptions')
    exceptions_overdue = fields.Integer(compute='_compute_role_figures', string='Overdue exceptions')
    arrivals_expected = fields.Integer(compute='_compute_role_figures', string='Trucks expected')
    lab_waiting = fields.Integer(compute='_compute_role_figures', string='Waiting in the lab')
    batches_on_hold = fields.Integer(compute='_compute_role_figures', string='Batches on quality hold')
    quarantine_kg = fields.Float(compute='_compute_role_figures', digits=(14, 0), string='Stock in quarantine (kg)')
    requests_open = fields.Integer(compute='_compute_role_figures', string='Purchase requests waiting')
    orders_late = fields.Integer(compute='_compute_role_figures', string='Deliveries overdue')
    vouchers_to_pay = fields.Integer(compute='_compute_role_figures', string='Vouchers ready to pay')
    amount_to_pay = fields.Float(compute='_compute_role_figures', digits=(14, 0), string='Value ready to pay')
    credit_notes_pending = fields.Integer(compute='_compute_role_figures', string='Credit notes waiting')
    holds_active = fields.Integer(compute='_compute_role_figures', string='Silos on treatment hold')
    dips_off = fields.Integer(compute='_compute_role_figures', string='Silo dips off the ledger')
    instruments_due = fields.Integer(compute='_compute_role_figures', string='Instruments due or overdue')

    def _count(self, model, domain):
        try:
            return self.env[model].search_count(domain)
        except (AccessError, KeyError):
            return 0

    def _compute_role_figures(self):
        env = self.env
        c = self._count
        mine = c('mos.exception', [('assignee_id', '=', env.uid), ('state', 'in', ('open', 'assigned'))])
        open_ = c('mos.exception', [('state', 'in', ('open', 'assigned'))])
        overdue = c('mos.exception', [('overdue', '=', True)])
        expected = c('mos.gate.visit', [('state', '=', 'expected')])
        lab = c('mos.lab.queue', [])
        held = c('mos.batch', [('state', '=', 'hold')])
        requests = c('mos.purchase.request', [('state', 'in', ('pending', 'approved'))])
        late = c('mos.po.outstanding', [('days_late', '>', 0)])
        credit_pending = c('mos.credit.note', [('state', 'in', ('draft', 'pending', 'approved'))])
        holds = c('mos.silo.hold', [('state', '=', 'active')])
        dips = c('mos.silo.dip', [('within_tolerance', '=', False), ('adjustment_id', '=', False)])
        try:
            instruments = len(env['mos.lab.instrument'].search([]).filtered(lambda i: i.state != 'ok'))
        except AccessError:
            instruments = 0
        try:
            vouchers = env['mos.payment.voucher'].search([('state', 'in', ('approved', 'partially_paid'))])
            to_pay = (len(vouchers), sum(vouchers.mapped('remaining_amount')))
        except AccessError:
            to_pay = (0, 0.0)
        try:
            quants = env['stock.quant'].sudo().search([('location_id.mos_stock_status', '!=', 'available'), ('location_id.usage', '=', 'internal')])
            quarantine = sum(quants.mapped('quantity'))
        except AccessError:
            quarantine = 0.0
        for rec in self:
            rec.exceptions_mine, rec.exceptions_open, rec.exceptions_overdue = mine, open_, overdue
            rec.arrivals_expected, rec.lab_waiting, rec.batches_on_hold = expected, lab, held
            rec.quarantine_kg = quarantine
            rec.requests_open, rec.orders_late = requests, late
            rec.vouchers_to_pay, rec.amount_to_pay = to_pay
            rec.credit_notes_pending, rec.holds_active, rec.dips_off, rec.instruments_due = credit_pending, holds, dips, instruments

    def action_open_exceptions(self):
        return self.env['ir.actions.actions']._for_xml_id('mos_controls.action_mos_exception')

    def action_open_lab_queue(self):
        return self.env['ir.actions.actions']._for_xml_id('mos_quality.action_mos_lab_queue')

    def action_open_vouchers(self):
        return self._open('mos.payment.voucher', self.env._('Vouchers ready to pay'), [('state', 'in', ('approved', 'partially_paid'))])

    def action_open_late_deliveries(self):
        return self.env['ir.actions.actions']._for_xml_id('mos_procurement_plus.action_mos_po_outstanding')
