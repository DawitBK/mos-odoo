from odoo import api, fields, models
from odoo.exceptions import UserError


class MosReceipt(models.Model):
    """Money received from a customer: against an invoice, or as an advance before delivery (FR-SAL-013).
    Gap-free numbered and immutable. A cash receipt belongs to the cashier's open session (FR-SAL-014)."""

    _name = 'mos.receipt'
    _inherit = ['mos.posted']
    _description = 'MOS customer receipt'
    _order = 'id desc'
    _mos_final_states = ('posted',)

    name = fields.Char(readonly=True, copy=False, default='New')
    customer_id = fields.Many2one('res.partner', required=True)
    invoice_id = fields.Many2one('mos.customer.invoice', domain="[('balance', '>', 0)]", help='Leave empty for an advance (cash before delivery).')
    amount = fields.Float(required=True)
    date = fields.Date(default=fields.Date.context_today)
    method = fields.Selection([('cash', 'Cash'), ('transfer', 'Bank transfer'), ('cheque', 'Cheque'), ('wallet', 'Mobile money')], required=True, default='cash')
    reference = fields.Char(string='Slip or reference')
    session_id = fields.Many2one('mos.cash.session', readonly=True)
    received_by_id = fields.Many2one('res.users', default=lambda s: s.env.user, readonly=True)
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals['amount'] <= 0:
                raise UserError(self.env._('A receipt must be for more than zero.'))
            invoice = self.env['mos.customer.invoice'].browse(vals.get('invoice_id'))
            if invoice:
                if invoice.customer_id.id != vals['customer_id']:
                    raise UserError(self.env._('That invoice belongs to another customer.'))
                if vals['amount'] > invoice.balance + 0.005:
                    raise UserError(self.env._('The receipt is more than the %s still owed on the invoice; record the rest as an advance.', invoice.balance))
            if vals.get('method', 'cash') == 'cash':
                session = self.env['mos.cash.session'].search([('user_id', '=', self.env.uid), ('state', '=', 'open')], limit=1)
                if not session:
                    raise UserError(self.env._('Open your cash session before taking cash.'))
                vals['session_id'] = session.id
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.receipt')
        return super().create(vals_list)


class MosCashSession(models.Model):
    """A cashier's till: open with a float, take receipts, count at close, explain any difference (FR-SAL-014)."""

    _name = 'mos.cash.session'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS cash session'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    user_id = fields.Many2one('res.users', required=True, default=lambda s: s.env.user, readonly=True)
    opened_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    closed_at = fields.Datetime(readonly=True)
    float_amount = fields.Float(string='Opening float', required=True)
    receipt_ids = fields.One2many('mos.receipt', 'session_id')
    cash_received = fields.Float(compute='_compute_expected', string='Cash received')
    expected_cash = fields.Float(compute='_compute_expected', string='Cash that should be in the till')
    counted_cash = fields.Float()
    variance = fields.Float(compute='_compute_expected')
    variance_reason = fields.Char()
    state = fields.Selection([('open', 'Open'), ('closed', 'Closed')], default='open', readonly=True, tracking=True)

    @api.depends('receipt_ids.amount', 'float_amount', 'counted_cash', 'state')
    def _compute_expected(self):
        for s in self:
            s.cash_received = sum(s.receipt_ids.filtered(lambda r: r.method == 'cash').mapped('amount'))
            s.expected_cash = s.float_amount + s.cash_received
            s.variance = round(s.counted_cash - s.expected_cash, 2) if s.state == 'closed' else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if self.search_count([('user_id', '=', vals.get('user_id', self.env.uid)), ('state', '=', 'open')]):
                raise UserError(self.env._('You already have an open cash session.'))
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.cash.session')
        return super().create(vals_list)

    def action_close_form(self):
        for s in self:
            s.action_close(s.counted_cash, s.variance_reason or '')

    def action_close(self, counted, reason=''):
        for s in self:
            if s.state != 'open':
                raise UserError(self.env._('This session is already closed.'))
            if s.user_id != self.env.user:
                raise UserError(self.env._('Only the cashier who opened the session can close it.'))
            variance = round(counted - s.expected_cash, 2)
            if abs(variance) > 0.005 and not reason:
                raise UserError(self.env._('The till is %(v)s off: write the reason before closing.', v=variance))
            s.write({'state': 'closed', 'closed_at': fields.Datetime.now(), 'counted_cash': counted, 'variance_reason': reason or False})
            self.env['mos.audit.event'].record('cash_session_closed', s, f'{s.name}: variance {variance}')
