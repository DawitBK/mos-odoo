import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosSupplierInvoice(models.Model):
    """A supplier's invoice, matched three ways: what was ordered, what was received, what is billed (FR-AP-001..004).

    The match is quantity and price against tolerances kept in system parameters. Inside tolerance the invoice is
    payable as billed; outside it, the buyer either asks for a debit note for the excess or sends the variance for
    approval through the matrix. The invoice number is normalised so the same supplier cannot bill the same number twice.
    """

    _name = 'mos.supplier.invoice'
    _inherit = ['mos.audited', 'mos.approvable', 'mail.thread']
    _description = 'MOS supplier invoice'
    _order = 'invoice_date desc, id desc'

    name = fields.Char(readonly=True, copy=False, default='New', string='Our reference')
    supplier_id = fields.Many2one('res.partner', required=True, domain="[('supplier_rank', '>', 0)]")
    supplier_ref = fields.Char(string='Supplier invoice number', required=True)
    ref_norm = fields.Char(compute='_compute_ref_norm', store=True, index=True)
    invoice_date = fields.Date(required=True, default=fields.Date.context_today)
    purchase_id = fields.Many2one('purchase.order', string='Purchase order', required=True)
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(string='Billed quantity (kg)', required=True, digits=(14, 1))
    unit_price = fields.Float(string='Billed price per kg', required=True, digits=(12, 4))
    vat_pct = fields.Float(string='VAT (%)')
    wht_pct = fields.Float(string='Withholding (%)')
    net_amount = fields.Float(compute='_compute_amounts', store=True, string='Net of tax')
    vat_amount = fields.Float(compute='_compute_amounts', store=True)
    gross_amount = fields.Float(compute='_compute_amounts', store=True, string='Gross (with VAT)')
    wht_amount = fields.Float(compute='_compute_amounts', store=True, string='Withheld')
    payable_amount = fields.Float(compute='_compute_amounts', store=True, string='Payable to supplier')

    ordered_qty = fields.Float(readonly=True, digits=(14, 1))
    received_qty = fields.Float(readonly=True, digits=(14, 1))
    ordered_price = fields.Float(readonly=True, digits=(12, 4))
    qty_variance = fields.Float(readonly=True, digits=(14, 1), string='Billed minus received (kg)')
    price_variance_pct = fields.Float(readonly=True, digits=(6, 2), string='Price above order (%)')
    match_state = fields.Selection([('unmatched', 'Not matched'), ('matched', 'Matched'), ('exception', 'Variance')], default='unmatched', readonly=True, tracking=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('payable', 'Payable'), ('vouchered', 'On a payment voucher'), ('paid', 'Paid'), ('cancelled', 'Cancelled')],
        default='draft',
        readonly=True,
        tracking=True,
    )
    debit_note_ids = fields.One2many('mos.debit.note', 'invoice_id')
    voucher_line_ids = fields.One2many('mos.payment.voucher.line', 'invoice_id', domain=[('live', '=', True)])
    paid_amount = fields.Float(compute='_compute_paid', string='Paid')

    @api.depends('supplier_ref')
    def _compute_ref_norm(self):
        for inv in self:
            inv.ref_norm = re.sub(r'[^A-Z0-9]', '', (inv.supplier_ref or '').upper())

    @api.depends('qty', 'unit_price', 'vat_pct', 'wht_pct', 'debit_note_ids.amount', 'debit_note_ids.state')
    def _compute_amounts(self):
        for inv in self:
            net = round(inv.qty * inv.unit_price, 2)
            vat = round(net * inv.vat_pct / 100.0, 2)
            inv.net_amount, inv.vat_amount, inv.gross_amount = net, vat, net + vat
            inv.wht_amount = round(net * inv.wht_pct / 100.0, 2)
            debited = sum(inv.debit_note_ids.filtered(lambda d: d.state == 'posted').mapped('amount'))
            inv.payable_amount = round(net + vat - inv.wht_amount - debited, 2)

    def _compute_paid(self):
        for inv in self:
            inv.paid_amount = sum(inv.voucher_line_ids.filtered(lambda l: l.voucher_id.state != 'cancelled').mapped('paid_amount'))

    _dup = models.Constraint('unique(supplier_id, ref_norm)', 'This supplier has already billed that invoice number.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.supplier.invoice')
            partner = self.env['res.partner'].browse(vals.get('supplier_id'))
            vals.setdefault('vat_pct', partner.mos_vat_pct)
            vals.setdefault('wht_pct', partner._mos_effective_wht_pct())
        return super().create(vals_list)

    @api.constrains('qty', 'unit_price')
    def _check_positive(self):
        for inv in self:
            if inv.qty <= 0 or inv.unit_price <= 0:
                raise ValidationError(self.env._('Quantity and price must be above zero.'))

    # -- three-way match ---------------------------------------------------------------------------------------------
    def _mos_doc_type(self):
        return 'mos.supplier.invoice'

    def _mos_approval_amount(self):
        self.ensure_one()
        return abs(self.qty_variance) * self.unit_price + max(self.price_variance_pct, 0) / 100 * self.net_amount

    def _mos_approval_required(self):
        self.ensure_one()
        return self.match_state == 'exception' and super()._mos_approval_required()

    def action_match(self):
        icp = self.env['ir.config_parameter'].sudo()
        tol_qty = icp.get_float('mos_payables.tolerance_qty_pct', 0.5)
        tol_price = icp.get_float('mos_payables.tolerance_price_pct', 1.0)
        for inv in self:
            if inv.state not in ('draft', 'payable') or inv.voucher_line_ids:
                raise UserError(self.env._('Only an invoice that is not yet on a voucher can be matched again.'))
            po = inv.purchase_id.sudo()
            lines = po.order_line.filtered(lambda l: l.product_id == inv.product_id)
            if not lines:
                raise UserError(self.env._('The purchase order has no line for %s.', inv.product_id.display_name))
            ordered = sum(lines.mapped('product_qty'))
            ordered_price = inv._mos_expected_price(po, inv.product_id, lines[0].price_unit)
            wait = inv._mos_match_wait(po, inv.product_id)
            if wait:
                inv.sudo().write({'match_state': wait[0], 'state': 'draft'})
                self.env['mos.audit.event'].record('invoice_held', inv, f'{inv.name}: {wait[1]}')
                continue
            moves = po.picking_ids.move_ids.filtered(lambda m: m.state == 'done' and m.product_id == inv.product_id and m.picking_type_id.code == 'incoming')
            received = sum(moves.mapped('quantity'))
            qty_var = inv.qty - received
            quality_kg = inv._mos_quality_deduction_kg(po, inv.product_id)
            auto_debit = bool(quality_kg) and qty_var > 0 and abs(qty_var - quality_kg) <= max(received * tol_qty / 100.0, 1.0)
            price_var = ((inv.unit_price - ordered_price) / ordered_price * 100.0) if ordered_price else 0.0
            exception = (received <= 0) or ((abs(qty_var) > received * tol_qty / 100.0) and not auto_debit) or (price_var > tol_price)
            inv.sudo().write(
                {
                    'ordered_qty': ordered,
                    'received_qty': received,
                    'ordered_price': ordered_price,
                    'qty_variance': qty_var,
                    'price_variance_pct': price_var,
                    'match_state': 'exception' if exception else 'matched',
                    'state': 'draft' if exception else 'payable',
                }
            )
            self.env['mos.audit.event'].record('invoice_matched', inv, f'{inv.name}: {inv.match_state}, qty {qty_var:+.1f} kg, price {price_var:+.2f}%')
            if auto_debit and not exception:
                # The excess is exactly the quality deduction: the supplier credits it by a debit note (FRD 9.5 #24).
                excess = round(qty_var * inv.unit_price, 2)
                self.env['mos.debit.note'].sudo().create(
                    {
                        'invoice_id': inv.id,
                        'supplier_id': inv.supplier_id.id,
                        'amount': round(excess * (1 + inv.vat_pct / 100.0), 2),
                        'reason': self.env._('Quality deduction on the received load'),
                    }
                )

    def _mos_expected_price(self, order, product, base_price):
        """Hook: the price the order allows after any quality adjustment."""
        return base_price

    def _mos_match_wait(self, order, product):
        """Hook: return (match state, reason) when the invoice must wait (no receipt yet, goods still in quality hold)."""
        return None

    def _mos_quality_deduction_kg(self, order, product):
        """Hook: kilograms taken off the received loads as quality deduction."""
        return 0.0

    def action_accept_variance(self):
        """After the approval chain accepted the variance, the invoice is payable as billed (FR-AP-004)."""
        for inv in self:
            if inv.match_state != 'exception' or inv.mos_approval_state != 'approved':
                raise UserError(self.env._('The variance needs approval first: submit it for approval.'))
            inv.sudo().write({'state': 'payable'})

    def action_debit_excess(self):
        """Pay only what was received at the ordered price: the excess goes back to the supplier as a debit note (FR-AP-005)."""
        for inv in self:
            if inv.match_state != 'exception':
                raise UserError(self.env._('There is no variance to debit.'))
            excess_qty = max(inv.qty - inv.received_qty, 0.0)
            excess_price = max(inv.unit_price - inv.ordered_price, 0.0) * min(inv.qty, inv.received_qty)
            excess = round(excess_qty * inv.unit_price + excess_price, 2)
            vat = round(excess * inv.vat_pct / 100.0, 2)
            if excess <= 0:
                raise UserError(self.env._('The billed amount is not above what was received and ordered.'))
            self.env['mos.debit.note'].sudo().create({'invoice_id': inv.id, 'supplier_id': inv.supplier_id.id, 'amount': round(excess + vat, 2), 'reason': self.env._('Billed above the quantity received or the price ordered')})
            inv.sudo().write({'state': 'payable'})

    def action_cancel(self):
        for inv in self:
            if inv.state in ('vouchered', 'paid') or inv.voucher_line_ids:
                raise UserError(self.env._('An invoice on a voucher cannot be cancelled.'))
            inv.sudo().write({'state': 'cancelled'})

    @api.model
    def _cron_refresh_paid(self):
        return True
