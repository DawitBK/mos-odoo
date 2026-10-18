from odoo import fields, models
from odoo.exceptions import UserError


class MosCustomerStatement(models.TransientModel):
    """A customer's statement for a period: what they owed at the start, every invoice, receipt and credit, and what they owe at the end (FR-SAL-010)."""

    _name = 'mos.customer.statement'
    _description = 'MOS customer statement'

    customer_id = fields.Many2one('res.partner', required=True)
    date_from = fields.Date(required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    opening = fields.Float(readonly=True)
    closing = fields.Float(readonly=True)
    line_ids = fields.One2many('mos.customer.statement.line', 'statement_id')

    def action_build(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._('The end date is before the start date.'))
        customer = self.customer_id.commercial_partner_id
        Invoice, Receipt, Credit = (self.env[m].sudo() for m in ('mos.customer.invoice', 'mos.receipt', 'mos.credit.note'))
        invoices = Invoice.search([('customer_id', '=', customer.id)])
        receipts = Receipt.search([('customer_id', '=', customer.id)])
        credits = Credit.search([('customer_id', '=', customer.id), ('state', '=', 'posted')])
        opening = (
            sum(invoices.filtered(lambda i: i.date < self.date_from).mapped('gross_amount'))
            - sum(receipts.filtered(lambda r: r.date < self.date_from).mapped('amount'))
            - sum(credits.filtered(lambda c: c.date < self.date_from).mapped('amount'))
        )
        rows = []
        for inv in invoices.filtered(lambda i: self.date_from <= i.date <= self.date_to):
            rows.append((inv.date, inv.id, self.env._('Invoice %s', inv.name), inv.gross_amount))
        for rec in receipts.filtered(lambda r: self.date_from <= r.date <= self.date_to):
            label = self.env._('Receipt %s', rec.name) + (self.env._(' (advance)') if not rec.invoice_id else '')
            rows.append((rec.date, rec.id, label, -rec.amount))
        for note in credits.filtered(lambda c: self.date_from <= c.date <= self.date_to):
            rows.append((note.date, note.id, self.env._('Credit note %s', note.name), -note.amount))
        rows.sort(key=lambda r: (r[0], r[2]))
        self.line_ids.unlink()
        balance, lines = opening, []
        for date, _id, label, amount in rows:
            balance += amount
            lines.append({'statement_id': self.id, 'date': date, 'label': label, 'amount': amount, 'balance': balance})
        self.env['mos.customer.statement.line'].create(lines)
        self.write({'opening': opening, 'closing': balance})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class MosCustomerStatementLine(models.TransientModel):
    _name = 'mos.customer.statement.line'
    _description = 'MOS customer statement line'
    _order = 'date, id'

    statement_id = fields.Many2one('mos.customer.statement', required=True, ondelete='cascade')
    date = fields.Date()
    label = fields.Char()
    amount = fields.Float()
    balance = fields.Float()
