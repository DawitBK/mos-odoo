from odoo import api, fields, models


class MosCustomerInvoice(models.Model):
    """An invoice to a customer, raised from a delivery that really left the gate (FR-SAL-008).

    Numbered without gaps at posting, never edited afterwards. The one thing that may be added later is the number of
    the external fiscal receipt (FR-SAL-012)."""

    _name = 'mos.customer.invoice'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS customer invoice'
    _order = 'id desc'
    _mos_final_states = ('posted',)
    _mos_editable_after_post = {'fiscal_receipt_ref'}

    name = fields.Char(readonly=True, copy=False, default='New')
    customer_id = fields.Many2one('res.partner', required=True)
    sale_id = fields.Many2one('sale.order')
    delivery_id = fields.Many2one('mos.delivery.order')
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(string='Quantity (kg)', digits=(14, 1), required=True)
    unit_price = fields.Float(digits=(12, 4), required=True)
    vat_pct = fields.Float(default=15.0)
    net_amount = fields.Float(compute='_compute_amounts', store=True)
    vat_amount = fields.Float(compute='_compute_amounts', store=True)
    gross_amount = fields.Float(compute='_compute_amounts', store=True)
    date = fields.Date(default=fields.Date.context_today)
    fiscal_receipt_ref = fields.Char(string='Fiscal receipt no.', help='The number printed by the fiscal device or the tax authority\'s system.')
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)
    receipt_ids = fields.One2many('mos.receipt', 'invoice_id')
    paid_amount = fields.Float(compute='_compute_paid', store=True)
    balance = fields.Float(compute='_compute_paid', store=True, string='Owed')

    @api.depends('qty', 'unit_price', 'vat_pct')
    def _compute_amounts(self):
        for inv in self:
            net = round(inv.qty * inv.unit_price, 2)
            vat = round(net * inv.vat_pct / 100.0, 2)
            inv.net_amount, inv.vat_amount, inv.gross_amount = net, vat, net + vat

    @api.depends('receipt_ids.amount', 'gross_amount')
    def _compute_paid(self):
        for inv in self:
            inv.paid_amount = sum(inv.receipt_ids.mapped('amount'))
            inv.balance = round(inv.gross_amount - inv.paid_amount, 2)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.customer.invoice')
        return super().create(vals_list)
