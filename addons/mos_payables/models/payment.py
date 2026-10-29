from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class MosPayment(models.Model):
    """Money leaving: recorded against an approved voucher, gap-free numbered, immutable once posted.

    Segregation of duties: the person who records the payment is not the one who raised the voucher or approved it.
    A cheque payment takes the next unused leaf of the chosen book (FR-AP-008, 012).

    One payment can pay several invoices of the voucher, with an amount for each: the allocation table of the client's payment
    screen (page 25). A payment made without a table pays the oldest invoice of the voucher first."""

    _name = 'mos.payment'
    _inherit = ['mos.posted']
    _description = 'MOS payment'
    _order = 'id desc'
    _mos_final_states = ('posted',)

    name = fields.Char(readonly=True, copy=False, default='New')
    voucher_id = fields.Many2one('mos.payment.voucher', required=True, ondelete='restrict')
    supplier_id = fields.Many2one(related='voucher_id.supplier_id', store=True)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, required=True, readonly=True)
    allocation_ids = fields.One2many('mos.payment.line', 'payment_id', string='Invoices paid')
    amount = fields.Float(compute='_compute_amount', store=True, readonly=False, required=True)
    amount_in_words = fields.Char(compute='_compute_amount_in_words')
    notes = fields.Text(string='Reference / notes')
    date = fields.Date(default=fields.Date.context_today, required=True)
    method = fields.Selection([('transfer', 'Bank transfer'), ('cheque', 'Cheque'), ('cash', 'Cash'), ('wallet', 'Mobile money')], required=True, default='transfer')
    account_id = fields.Many2one('mos.paying.account', required=True)
    cheque_book_id = fields.Many2one('mos.cheque.book', domain="[('account_id', '=', account_id)]")
    cheque_leaf_id = fields.Many2one('mos.cheque.leaf', readonly=True)
    cheque_number = fields.Char(related='cheque_leaf_id.full_number', store=True)
    reference = fields.Char(help='Bank or wallet transaction reference.')
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)
    # what the screen shows around the amount
    total_invoice = fields.Float(compute='_compute_totals', string='Total invoice amount')
    total_previous = fields.Float(compute='_compute_totals', string='Total previously paid')
    total_balance = fields.Float(compute='_compute_totals', string='Total balance due')
    purchase_refs = fields.Char(compute='_compute_related_documents', string='Purchase orders')
    invoice_refs = fields.Char(compute='_compute_related_documents', string='Supplier invoices')
    approval_by = fields.Char(compute='_compute_approval', string='Approved by')
    approval_at = fields.Datetime(compute='_compute_approval', string='Approved on')
    approval_state = fields.Selection(related='voucher_id.mos_approval_state', string='Approval status')
    approval_comment = fields.Text(compute='_compute_approval', string='Approval comments')

    @api.depends('allocation_ids.amount')
    def _compute_amount(self):
        for pay in self:
            pay.amount = round(sum(pay.allocation_ids.mapped('amount')), 2) if pay.allocation_ids else pay.amount

    @api.depends('amount', 'currency_id')
    def _compute_amount_in_words(self):
        for pay in self:
            pay.amount_in_words = self.env._('%s Only', pay.currency_id.amount_to_text(pay.amount).title()) if pay.amount and pay.currency_id else False

    @api.depends('allocation_ids.invoice_amount', 'allocation_ids.previously_paid', 'allocation_ids.balance_due')
    def _compute_totals(self):
        for pay in self:
            pay.total_invoice = sum(pay.allocation_ids.mapped('invoice_amount'))
            pay.total_previous = sum(pay.allocation_ids.mapped('previously_paid'))
            pay.total_balance = sum(pay.allocation_ids.mapped('balance_due'))

    @api.depends('allocation_ids.voucher_line_id')
    def _compute_related_documents(self):
        for pay in self:
            invoices = pay.allocation_ids.voucher_line_id.sudo().invoice_id
            pay.purchase_refs = ', '.join(sorted(set(invoices.purchase_id.mapped('name'))))
            pay.invoice_refs = ', '.join(invoices.mapped('supplier_ref'))

    @api.depends('voucher_id.mos_approval_request_id.step_ids.decided_at')
    def _compute_approval(self):
        for pay in self:
            steps = pay.voucher_id.sudo().mos_approval_request_id.step_ids.filtered('decided_by_id').sorted('decided_at')
            pay.approval_by = ', '.join(steps.decided_by_id.mapped('name'))
            pay.approval_at = steps[-1:].decided_at
            pay.approval_comment = '\n'.join(comment for comment in steps.mapped('comment') if comment)

    @api.onchange('voucher_id')
    def _onchange_voucher_id(self):
        """The invoices of the voucher still to be paid, each with what is owed on it (the screen's "Invoices to be paid")."""
        commands = [Command.clear()]
        for line in self.voucher_id.line_ids.filtered(lambda l: l.balance > 0.005):
            commands.append(Command.create({'voucher_line_id': line.id, 'include': True, 'amount': line.balance, 'previously_paid': line.paid_amount, 'balance_due': line.balance}))
        self.allocation_ids = commands

    def _allocation_commands(self, voucher, vals):
        """The allocation table to store: the one the person filled in (rows that pay nothing are dropped), or, when there is none, the
        amount spread over the invoices of the voucher, oldest first. The amount of the payment and the table always agree."""
        given = [cmd[2] for cmd in vals.get('allocation_ids') or [] if cmd[0] == Command.CREATE and cmd[2].get('include', True) and (cmd[2].get('amount') or 0) > 0]
        if given:
            seen = set()
            for row in given:
                line = voucher.line_ids.filtered(lambda l: l.id == row.get('voucher_line_id'))
                if not line:
                    raise UserError(self.env._('Every invoice paid must be on the voucher.'))
                if line.id in seen:
                    raise UserError(self.env._('An invoice appears twice in the payment.'))
                seen.add(line.id)
                if row['amount'] > line.balance + 0.005:
                    raise UserError(self.env._('%(invoice)s has only %(balance)s left to pay.', invoice=line.invoice_id.name, balance=line.balance))
            total = round(sum(row['amount'] for row in given), 2)
            if vals.get('amount') and abs(vals['amount'] - total) > 0.005:
                raise UserError(self.env._('The payment amount (%(amount)s) must equal the sum of the amounts paid on the invoices (%(total)s).', amount=vals['amount'], total=total))
            vals['amount'] = total
        else:
            left = vals.get('amount') or 0.0
            for line in voucher.line_ids:
                take = round(min(line.balance, left), 2)
                if take > 0.005:
                    given.append({'voucher_line_id': line.id, 'amount': take})
                    left -= take
            if given and left > 0.005:  # a rounding cent on the last invoice
                given[-1]['amount'] = round(given[-1]['amount'] + left, 2)
        lines = {line.id: line for line in voucher.line_ids}
        return [
            Command.create({'voucher_line_id': row['voucher_line_id'], 'amount': row['amount'], 'previously_paid': lines[row['voucher_line_id']].paid_amount, 'balance_due': lines[row['voucher_line_id']].balance})
            for row in given
        ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            voucher = self.env['mos.payment.voucher'].browse(vals['voucher_id'])
            if voucher.state not in ('approved', 'partially_paid'):
                raise UserError(self.env._('Payments can only be made against an approved voucher (this one is %s).', voucher.state))
            vals['allocation_ids'] = self._allocation_commands(voucher, vals)
            if (vals.get('amount') or 0) <= 0 or vals['amount'] > voucher.remaining_amount + 0.005:
                raise UserError(self.env._('The payment must be above zero and not more than the %s still owed.', voucher.remaining_amount))
            uid = self.env.uid
            approvers = voucher.mos_approval_request_id.sudo().step_ids.mapped('decided_by_id')
            if uid == voucher.create_uid.id or uid in approvers.ids:
                raise UserError(self.env._('The person who raised or approved the voucher cannot record its payment.'))
            if vals.get('method') == 'cheque':
                book = self.env['mos.cheque.book'].browse(vals.get('cheque_book_id'))
                if not book or book.account_id.id != vals['account_id']:
                    raise UserError(self.env._('Choose a cheque book of the paying account.'))
                vals['cheque_leaf_id'] = book.next_leaf().id
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.payment')
        payments = super().create(vals_list)
        for pay in payments:
            if pay.cheque_leaf_id:
                pay.cheque_leaf_id.sudo().write({'state': 'issued', 'payment_id': pay.id})
            pay.voucher_id.invalidate_recordset(['paid_amount', 'remaining_amount'])
            pay.voucher_id._refresh_state()
            self.env['mos.audit.event'].record('payment_posted', pay, f'{pay.name}: {pay.amount} to {pay.supplier_id.name}')
        return payments


class MosPaymentLine(models.Model):
    """The part of a payment that settles one invoice of the voucher, with what was owed on it before the payment: the allocation table
    of the payment screen. Never changed afterwards; a mistake is reversed with the whole payment."""

    _name = 'mos.payment.line'
    _description = 'MOS payment: invoice paid'
    _order = 'id'

    payment_id = fields.Many2one('mos.payment', ondelete='cascade', index=True)
    voucher_line_id = fields.Many2one('mos.payment.voucher.line', required=True, ondelete='restrict', index=True)
    include = fields.Boolean(string='Pay', default=True)
    invoice_id = fields.Many2one(related='voucher_line_id.invoice_id')
    invoice_date = fields.Date(related='voucher_line_id.invoice_id.invoice_date')
    purchase_id = fields.Many2one(related='voucher_line_id.invoice_id.purchase_id', string='PO number')
    grn_numbers = fields.Char(related='voucher_line_id.invoice_id.grn_numbers', string='GRN number')
    invoice_amount = fields.Float(related='voucher_line_id.net', string='Invoice amount')
    previously_paid = fields.Float(readonly=True)
    balance_due = fields.Float(readonly=True)
    amount = fields.Float(string='Amount to pay')

    @api.onchange('include')
    def _onchange_include(self):
        self.amount = self.balance_due if self.include else 0.0

    def write(self, vals):
        raise UserError(self.env._('A payment cannot be changed. Reverse it and make a new one.'))

    def unlink(self):
        if self.env.context.get('module'):  # uninstall
            return super().unlink()
        raise UserError(self.env._('A payment cannot be changed. Reverse it and make a new one.'))
