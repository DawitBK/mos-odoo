import re
from datetime import timedelta

from odoo import Command, api, fields, models
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
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, required=True, readonly=True)
    delivery_note_ref = fields.Char(string='Delivery note')
    payment_terms_days = fields.Integer(string='Payment terms (days)', default=30, help='How many days after the invoice date the supplier expects payment.')
    due_date = fields.Date(compute='_compute_due_date', store=True)
    line_ids = fields.One2many('mos.supplier.invoice.line', 'invoice_id', string='Items', copy=True)
    vat_pct = fields.Float(string='VAT (%)')
    wht_pct = fields.Float(string='Withholding (%)')
    net_amount = fields.Float(compute='_compute_amounts', store=True, string='Net of tax')
    vat_amount = fields.Float(compute='_compute_amounts', store=True)
    gross_amount = fields.Float(compute='_compute_amounts', store=True, string='Gross (with VAT)')
    wht_amount = fields.Float(compute='_compute_amounts', store=True, string='Withheld')
    payable_amount = fields.Float(compute='_compute_amounts', store=True, string='Payable to supplier')

    # What a one-item invoice says about its item: the first item, shown only when the invoice bills a single item. Several items are
    # read line by line (``line_ids``), each with its own match.
    product_id = fields.Many2one('product.product', compute='_compute_single_item', string='Item')
    qty = fields.Float(compute='_compute_single_item', string='Billed quantity', digits=(14, 1))
    unit_price = fields.Float(compute='_compute_single_item', string='Billed price', digits=(12, 4))
    ordered_qty = fields.Float(compute='_compute_single_item', digits=(14, 1))
    received_qty = fields.Float(compute='_compute_single_item', digits=(14, 1))
    ordered_price = fields.Float(compute='_compute_single_item', digits=(12, 4))
    qty_variance = fields.Float(compute='_compute_single_item', digits=(14, 1), string='Billed minus received')
    price_variance_pct = fields.Float(compute='_compute_single_item', digits=(6, 2), string='Price above order (%)')
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
    amount_due = fields.Float(compute='_compute_paid', string='Amount due')
    grn_numbers = fields.Char(compute='_compute_grn_numbers', string='Goods receipts', help='The receipts of the purchase order that brought the goods in.')

    @api.depends('supplier_ref')
    def _compute_ref_norm(self):
        for inv in self:
            inv.ref_norm = re.sub(r'[^A-Z0-9]', '', (inv.supplier_ref or '').upper())

    @api.depends('line_ids.net_amount', 'vat_pct', 'wht_pct', 'debit_note_ids.amount', 'debit_note_ids.state')
    def _compute_amounts(self):
        for inv in self:
            net = round(sum(inv.line_ids.mapped('net_amount')), 2)
            vat = round(net * inv.vat_pct / 100.0, 2)
            inv.net_amount, inv.vat_amount, inv.gross_amount = net, vat, net + vat
            inv.wht_amount = round(net * inv.wht_pct / 100.0, 2)
            debited = sum(inv.debit_note_ids.filtered(lambda d: d.state == 'posted').mapped('amount'))
            inv.payable_amount = round(net + vat - inv.wht_amount - debited, 2)

    @api.onchange('supplier_id')
    def _onchange_supplier_id(self):
        """The tax profile and payment terms of the supplier (the same defaults a created invoice takes)."""
        if self.supplier_id:
            self.vat_pct = self.supplier_id.mos_vat_pct
            self.wht_pct = self.supplier_id._mos_effective_wht_pct()
            self.payment_terms_days = self.supplier_id.mos_payment_terms_days

    @api.depends('invoice_date', 'payment_terms_days')
    def _compute_due_date(self):
        for inv in self:
            inv.due_date = inv.invoice_date and inv.invoice_date + timedelta(days=inv.payment_terms_days or 0)

    @api.depends('line_ids.product_id', 'line_ids.qty', 'line_ids.unit_price', 'line_ids.ordered_qty', 'line_ids.received_qty', 'line_ids.ordered_price', 'line_ids.qty_variance', 'line_ids.price_variance_pct')
    def _compute_single_item(self):
        for inv in self:
            line = inv.line_ids if len(inv.line_ids) == 1 else inv.line_ids.browse()
            inv.product_id = line.product_id
            for name in ('qty', 'unit_price', 'ordered_qty', 'received_qty', 'ordered_price', 'qty_variance', 'price_variance_pct'):
                inv[name] = line[name] if line else 0.0

    def _compute_grn_numbers(self):
        for inv in self:
            receipts = inv.purchase_id.sudo().picking_ids.filtered(lambda p: p.picking_type_id.code == 'incoming' and p.state == 'done')
            inv.grn_numbers = ', '.join(receipts.mapped('name'))

    def _compute_paid(self):
        for inv in self:
            inv.paid_amount = sum(inv.voucher_line_ids.filtered(lambda l: l.voucher_id.state != 'cancelled').mapped('paid_amount'))
            inv.amount_due = round(inv.payable_amount - inv.paid_amount, 2)

    _dup = models.Constraint('unique(supplier_id, ref_norm)', 'This supplier has already billed that invoice number.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.supplier.invoice')
            partner = self.env['res.partner'].browse(vals.get('supplier_id'))
            vals.setdefault('vat_pct', partner.mos_vat_pct)
            vals.setdefault('wht_pct', partner._mos_effective_wht_pct())
            vals.setdefault('payment_terms_days', partner.mos_payment_terms_days)
            if 'line_ids' not in vals and vals.get('product_id'):  # an invoice that bills one item, said in one go
                vals['line_ids'] = [Command.create({'product_id': vals['product_id'], 'qty': vals.get('qty', 0.0), 'unit_price': vals.get('unit_price', 0.0)})]
            for name in ('product_id', 'qty', 'unit_price'):
                vals.pop(name, None)
        invoices = super().create(vals_list)
        for inv in invoices:
            if not inv.line_ids:
                raise ValidationError(self.env._('An invoice bills at least one item.'))
        return invoices

    # -- three-way match ---------------------------------------------------------------------------------------------
    def _mos_doc_type(self):
        return 'mos.supplier.invoice'

    def _mos_approval_amount(self):
        self.ensure_one()
        return sum(abs(line.qty_variance) * line.unit_price + max(line.price_variance_pct, 0) / 100 * line.net_amount for line in self.line_ids)

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
            results = []
            held = None
            for line in inv.line_ids:
                po_lines = po.order_line.filtered(lambda l: l.product_id == line.product_id)
                if not po_lines:
                    raise UserError(self.env._('The purchase order has no line for %s.', line.product_id.display_name))
                held = inv._mos_match_wait(po, line.product_id)
                if held:
                    break
                ordered_price = inv._mos_expected_price(po, line.product_id, po_lines[0].price_unit)
                moves = po.picking_ids.move_ids.filtered(lambda m: m.state == 'done' and m.product_id == line.product_id and m.picking_type_id.code == 'incoming')
                received = sum(moves.mapped('quantity'))
                qty_var = line.qty - received
                quality_kg = inv._mos_quality_deduction_kg(po, line.product_id)
                auto_debit = bool(quality_kg) and qty_var > 0 and abs(qty_var - quality_kg) <= max(received * tol_qty / 100.0, 1.0)
                price_var = ((line.unit_price - ordered_price) / ordered_price * 100.0) if ordered_price else 0.0
                exception = (received <= 0) or ((abs(qty_var) > received * tol_qty / 100.0) and not auto_debit) or (price_var > tol_price)
                values = {
                    'ordered_qty': sum(po_lines.mapped('product_qty')),
                    'received_qty': received,
                    'ordered_price': ordered_price,
                    'qty_variance': qty_var,
                    'price_variance_pct': price_var,
                    'match_state': 'exception' if exception else 'matched',
                }
                results.append((line, auto_debit, exception, values))
            if held:
                inv.sudo().write({'match_state': held[0], 'state': 'draft'})
                self.env['mos.audit.event'].record('invoice_held', inv, f'{inv.name}: {held[1]}')
                continue
            exception = any(result[2] for result in results)
            for line, _auto, _exc, values in results:
                line.sudo().write(values)
            inv.sudo().write({'match_state': 'exception' if exception else 'matched', 'state': 'draft' if exception else 'payable'})
            summary = '; '.join(f'{line.product_id.display_name} {values["qty_variance"]:+.1f}, price {values["price_variance_pct"]:+.2f}%' for line, _auto, _exc, values in results)
            self.env['mos.audit.event'].record('invoice_matched', inv, f'{inv.name}: {inv.match_state}: {summary}')
            excess = round(sum(line.unit_price * values['qty_variance'] for line, auto, _exc, values in results if auto), 2)
            if excess > 0 and not exception:
                # The excess is exactly the quality deduction: the supplier credits it by a debit note (FRD 9.5 #24).
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
            excess = 0.0
            for line in inv.line_ids:
                excess_qty = max(line.qty - line.received_qty, 0.0)
                excess_price = max(line.unit_price - line.ordered_price, 0.0) * min(line.qty, line.received_qty)
                excess += excess_qty * line.unit_price + excess_price
            excess = round(excess, 2)
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


class MosSupplierInvoiceLine(models.Model):
    """One item of a supplier's invoice, matched on its own against the order line and the receipts of the same product (client screen 21)."""

    _name = 'mos.supplier.invoice.line'
    _description = 'MOS supplier invoice item'
    _order = 'sequence, id'

    invoice_id = fields.Many2one('mos.supplier.invoice', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Item', required=True)
    description = fields.Char()
    uom_id = fields.Many2one(related='product_id.uom_id', string='UoM')
    qty = fields.Float(string='Billed quantity', required=True, digits=(14, 1))
    unit_price = fields.Float(string='Unit price', required=True, digits=(12, 4))
    tax_pct = fields.Float(related='invoice_id.vat_pct', string='Tax (%)')
    net_amount = fields.Float(compute='_compute_amount', store=True, string='Net of tax')
    total_amount = fields.Float(compute='_compute_amount', store=True, string='Total amount')
    ordered_qty = fields.Float(readonly=True, digits=(14, 1))
    received_qty = fields.Float(readonly=True, digits=(14, 1), string='Received (accepted)')
    ordered_price = fields.Float(readonly=True, digits=(12, 4))
    qty_variance = fields.Float(readonly=True, digits=(14, 1), string='Billed minus received')
    price_variance_pct = fields.Float(readonly=True, digits=(6, 2), string='Price above order (%)')
    match_state = fields.Selection([('matched', 'Matched'), ('exception', 'Variance')], readonly=True, string='Match')

    _item_once = models.Constraint('unique(invoice_id, product_id)', 'An item appears once on an invoice.')

    @api.depends('qty', 'unit_price', 'invoice_id.vat_pct')
    def _compute_amount(self):
        for line in self:
            line.net_amount = round(line.qty * line.unit_price, 2)
            line.total_amount = round(line.net_amount * (1 + line.invoice_id.vat_pct / 100.0), 2)

    @api.constrains('qty', 'unit_price')
    def _check_positive(self):
        for line in self:
            if line.qty <= 0 or line.unit_price <= 0:
                raise ValidationError(self.env._('Quantity and price must be above zero.'))

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """The price the order allows, when the order is known."""
        order = self.invoice_id.purchase_id
        lines = order.order_line.filtered(lambda l: l.product_id == self.product_id)[:1]
        if lines:
            self.unit_price = lines.price_unit
            self.qty = self.qty or lines.product_qty
        self.description = self.description or self.product_id.display_name
