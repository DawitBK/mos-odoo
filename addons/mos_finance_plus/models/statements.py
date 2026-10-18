from odoo import api, fields, models
from odoo.exceptions import UserError


class MosSupplierStatement(models.TransientModel):
    """What we owe a supplier over a period: the balance at the start, each invoice, debit note and payment, the balance at the end (FR-AP-009)."""

    _name = 'mos.supplier.statement'
    _description = 'MOS supplier statement'

    supplier_id = fields.Many2one('res.partner', required=True, domain="[('supplier_rank', '>', 0)]")
    date_from = fields.Date(required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    opening = fields.Float(readonly=True)
    closing = fields.Float(readonly=True)
    line_ids = fields.One2many('mos.supplier.statement.line', 'statement_id')

    def action_build(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._('The end date is before the start date.'))
        supplier = self.supplier_id
        Invoice, Note, Payment = (self.env[m].sudo() for m in ('mos.supplier.invoice', 'mos.debit.note', 'mos.payment'))
        invoices = Invoice.search([('supplier_id', '=', supplier.id), ('state', 'not in', ('draft', 'cancelled'))])
        notes = Note.search([('supplier_id', '=', supplier.id)])
        payments = Payment.search([('supplier_id', '=', supplier.id)])
        gross = lambda inv: inv.payable_amount + sum(inv.debit_note_ids.mapped('amount'))  # the invoice before its debit notes
        opening = (
            sum(gross(i) for i in invoices if i.invoice_date < self.date_from)
            - sum(n.amount for n in notes if n.date < self.date_from)
            - sum(p.amount for p in payments if p.date < self.date_from)
        )
        rows = []
        for inv in invoices.filtered(lambda i: self.date_from <= i.invoice_date <= self.date_to):
            rows.append((inv.invoice_date, self.env._('Invoice %(ref)s (%(name)s)', ref=inv.supplier_ref, name=inv.name), gross(inv)))
        for note in notes.filtered(lambda n: self.date_from <= n.date <= self.date_to):
            rows.append((note.date, self.env._('Debit note %s', note.name), -note.amount))
        for pay in payments.filtered(lambda p: self.date_from <= p.date <= self.date_to):
            rows.append((pay.date, self.env._('Payment %s', pay.name), -pay.amount))
        rows.sort(key=lambda r: (r[0], r[1]))
        self.line_ids.unlink()
        balance, lines = opening, []
        for date, label, amount in rows:
            balance += amount
            lines.append({'statement_id': self.id, 'date': date, 'label': label, 'amount': amount, 'balance': balance})
        self.env['mos.supplier.statement.line'].create(lines)
        self.write({'opening': opening, 'closing': balance})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class MosSupplierStatementLine(models.TransientModel):
    _name = 'mos.supplier.statement.line'
    _description = 'MOS supplier statement line'
    _order = 'date, id'

    statement_id = fields.Many2one('mos.supplier.statement', required=True, ondelete='cascade')
    date = fields.Date()
    label = fields.Char()
    amount = fields.Float()
    balance = fields.Float()


class MosGrni(models.TransientModel):
    """Goods received, not invoiced, as of a date: every posted receipt against what was billed for it, and the invoices that
    arrived with no receipt behind them (FR-AP-019)."""

    _name = 'mos.grni'
    _description = 'MOS GRNI reconciliation'

    as_of = fields.Date(required=True, default=fields.Date.context_today)
    received_total = fields.Float(readonly=True, string='Received value')
    invoiced_total = fields.Float(readonly=True, string='Invoiced value')
    grni_total = fields.Float(readonly=True, string='Received, not invoiced')
    unreceived_total = fields.Float(readonly=True, string='Invoiced, not received')
    line_ids = fields.One2many('mos.grni.line', 'grni_id')

    def action_build(self):
        self.ensure_one()
        end = fields.Datetime.to_datetime(self.as_of).replace(hour=23, minute=59, second=59)
        self.line_ids.unlink()
        rows = []
        orders = self.env['purchase.order'].sudo().search([('state', 'in', ('purchase', 'done'))])
        for order in orders:
            for line in order.order_line.filtered(lambda l: l.product_id.is_storable):
                moves = order.picking_ids.move_ids.filtered(lambda m: m.state == 'done' and m.product_id == line.product_id and m.picking_type_id.code == 'incoming' and m.date <= end)
                received_qty = sum(moves.mapped('quantity'))
                invoices = self.env['mos.supplier.invoice'].sudo().search(
                    [('purchase_id', '=', order.id), ('product_id', '=', line.product_id.id), ('state', 'not in', ('draft', 'cancelled')), ('invoice_date', '<=', self.as_of)]
                )
                invoiced_qty = sum(invoices.mapped('qty'))
                if not received_qty and not invoiced_qty:
                    continue
                price = line.price_unit
                rows.append(
                    {
                        'grni_id': self.id,
                        'order_id': order.id,
                        'supplier_id': order.partner_id.id,
                        'product_id': line.product_id.id,
                        'received_qty': received_qty,
                        'invoiced_qty': invoiced_qty,
                        'received_value': round(received_qty * price, 2),
                        'invoiced_value': round(sum(invoices.mapped('net_amount')), 2),
                    }
                )
        self.env['mos.grni.line'].create(rows)
        lines = self.line_ids
        self.write(
            {
                'received_total': sum(lines.mapped('received_value')),
                'invoiced_total': sum(lines.mapped('invoiced_value')),
                'grni_total': sum(lines.mapped('grni_value')),
                'unreceived_total': sum(lines.mapped('unreceived_value')),
            }
        )
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class MosGrniLine(models.TransientModel):
    _name = 'mos.grni.line'
    _description = 'MOS GRNI line'
    _order = 'grni_value desc, id'

    grni_id = fields.Many2one('mos.grni', required=True, ondelete='cascade')
    order_id = fields.Many2one('purchase.order')
    supplier_id = fields.Many2one('res.partner')
    product_id = fields.Many2one('product.product')
    received_qty = fields.Float(digits=(14, 1))
    invoiced_qty = fields.Float(digits=(14, 1))
    received_value = fields.Float()
    invoiced_value = fields.Float()
    grni_value = fields.Float(compute='_compute_values', store=True, string='Received, not invoiced')
    unreceived_value = fields.Float(compute='_compute_values', store=True, string='Invoiced, not received')

    @api.depends('received_value', 'invoiced_value')
    def _compute_values(self):
        for line in self:
            diff = line.received_value - line.invoiced_value
            line.grni_value = max(diff, 0.0)
            line.unreceived_value = max(-diff, 0.0)
