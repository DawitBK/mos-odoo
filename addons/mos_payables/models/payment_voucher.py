from odoo import api, fields, models
from odoo.exceptions import UserError


class MosPaymentVoucher(models.Model):
    """A request to pay: DRAFT, SUBMITTED for approval, APPROVED, then PARTIALLY_PAID or PAID (FR-AP-007, 013, 014).

    Each line shows gross, VAT, withholding and the net actually paid to the supplier. A payment can only be made
    against an approved voucher."""

    _name = 'mos.payment.voucher'
    _inherit = ['mos.audited', 'mos.approvable', 'mail.thread']
    _description = 'MOS payment voucher'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    supplier_id = fields.Many2one('res.partner', required=True, domain="[('supplier_rank', '>', 0)]")
    line_ids = fields.One2many('mos.payment.voucher.line', 'voucher_id', string='Invoices')
    date = fields.Date(default=fields.Date.context_today)
    gross_amount = fields.Float(compute='_compute_totals', store=True)
    vat_amount = fields.Float(compute='_compute_totals', store=True)
    wht_amount = fields.Float(compute='_compute_totals', store=True, string='Withheld')
    net_amount = fields.Float(compute='_compute_totals', store=True, string='Net payable')
    paid_amount = fields.Float(compute='_compute_paid', store=True)
    remaining_amount = fields.Float(compute='_compute_paid', store=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('partially_paid', 'Partially paid'), ('paid', 'Paid'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft',
        readonly=True,
        tracking=True,
    )
    payment_ids = fields.One2many('mos.payment', 'voucher_id')

    @api.depends('line_ids.gross', 'line_ids.vat', 'line_ids.wht', 'line_ids.net')
    def _compute_totals(self):
        for v in self:
            v.gross_amount = sum(v.line_ids.mapped('gross'))
            v.vat_amount = sum(v.line_ids.mapped('vat'))
            v.wht_amount = sum(v.line_ids.mapped('wht'))
            v.net_amount = sum(v.line_ids.mapped('net'))

    @api.depends('payment_ids.amount', 'net_amount')
    def _compute_paid(self):
        for v in self:
            v.paid_amount = sum(v.payment_ids.mapped('amount'))
            v.remaining_amount = round(v.net_amount - v.paid_amount, 2)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.payment.voucher')
        return super().create(vals_list)

    # -- the approval chain: gross amount, matrix by amount band ---------------------------------------------------
    def _mos_doc_type(self):
        return 'mos.payment.voucher'

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.gross_amount

    def action_submit(self):
        for v in self:
            if v.state != 'draft':
                raise UserError(self.env._('Only a draft voucher can be submitted.'))
            if not v.line_ids:
                raise UserError(self.env._('Add at least one invoice to the voucher.'))
            if any(l.invoice_id.state != 'payable' or l.invoice_id.supplier_id != v.supplier_id for l in v.line_ids):
                raise UserError(self.env._('Every invoice must be payable and belong to the voucher\'s supplier.'))
            v.sudo().write({'state': 'submitted'})
            v.line_ids.invoice_id.sudo().write({'state': 'vouchered'})
            if v._mos_approval_required():
                v.action_mos_submit_approval()
            else:  # below every band: nothing to approve
                v.sudo().write({'state': 'approved'})
            self.env['mos.audit.event'].record('voucher_submitted', v, f'{v.name}: {v.gross_amount}')

    def _mos_approval_done(self, request):
        super()._mos_approval_done(request)
        self.sudo().write({'state': 'approved'})

    def _mos_approval_closed(self, request, state):
        super()._mos_approval_closed(request, state)
        if state in ('rejected', 'returned', 'cancelled'):
            # The invoices are freed so they can go on another voucher.
            self.sudo().write({'state': 'rejected' if state == 'rejected' else 'draft'})
            self.line_ids.invoice_id.sudo().write({'state': 'payable'})
            if state == 'rejected':
                self.line_ids.sudo().write({'live': False})

    def action_cancel(self):
        for v in self:
            if v.payment_ids:
                raise UserError(self.env._('A voucher with payments cannot be cancelled.'))
            if v.state == 'cancelled':
                continue
            v.sudo().write({'state': 'cancelled'})
            v.line_ids.invoice_id.sudo().write({'state': 'payable'})
            v.line_ids.sudo().write({'live': False})

    def _refresh_state(self):
        for v in self:
            if v.state in ('approved', 'partially_paid', 'paid'):
                paid = v.paid_amount
                state = 'paid' if v.remaining_amount <= 0.005 else 'partially_paid' if paid > 0 else 'approved'
                v.sudo().write({'state': state})
                if state == 'paid':
                    v.line_ids.invoice_id.sudo().write({'state': 'paid'})


class MosPaymentVoucherLine(models.Model):
    _name = 'mos.payment.voucher.line'
    _description = 'MOS payment voucher line'

    voucher_id = fields.Many2one('mos.payment.voucher', required=True, ondelete='cascade')
    invoice_id = fields.Many2one('mos.supplier.invoice', required=True, ondelete='restrict', domain="[('state', '=', 'payable')]")
    gross = fields.Float(related='invoice_id.gross_amount', store=True)
    vat = fields.Float(related='invoice_id.vat_amount', store=True)
    wht = fields.Float(related='invoice_id.wht_amount', store=True)
    net = fields.Float(related='invoice_id.payable_amount', store=True)
    paid_amount = fields.Float(compute='_compute_paid')

    live = fields.Boolean(default=True, help='False once the voucher is cancelled or rejected: the invoice is free again.')

    _invoice_once = models.UniqueIndex('(invoice_id) WHERE live', 'An invoice can be on only one live voucher.')

    def _compute_paid(self):
        for line in self:
            total = line.voucher_id.net_amount
            line.paid_amount = (line.voucher_id.paid_amount * line.net / total) if total else 0.0
