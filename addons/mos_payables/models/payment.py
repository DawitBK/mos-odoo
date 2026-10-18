from odoo import api, fields, models
from odoo.exceptions import UserError


class MosPayment(models.Model):
    """Money leaving: recorded against an approved voucher, gap-free numbered, immutable once posted.

    Segregation of duties: the person who records the payment is not the one who raised the voucher or approved it.
    A cheque payment takes the next unused leaf of the chosen book (FR-AP-008, 012)."""

    _name = 'mos.payment'
    _inherit = ['mos.posted']
    _description = 'MOS payment'
    _order = 'id desc'
    _mos_final_states = ('posted',)

    name = fields.Char(readonly=True, copy=False, default='New')
    voucher_id = fields.Many2one('mos.payment.voucher', required=True, ondelete='restrict')
    supplier_id = fields.Many2one(related='voucher_id.supplier_id', store=True)
    amount = fields.Float(required=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    method = fields.Selection([('transfer', 'Bank transfer'), ('cheque', 'Cheque'), ('cash', 'Cash'), ('wallet', 'Mobile money')], required=True, default='transfer')
    account_id = fields.Many2one('mos.paying.account', required=True)
    cheque_book_id = fields.Many2one('mos.cheque.book', domain="[('account_id', '=', account_id)]")
    cheque_leaf_id = fields.Many2one('mos.cheque.leaf', readonly=True)
    cheque_number = fields.Char(related='cheque_leaf_id.full_number', store=True)
    reference = fields.Char(help='Bank or wallet transaction reference.')
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            voucher = self.env['mos.payment.voucher'].browse(vals['voucher_id'])
            if voucher.state not in ('approved', 'partially_paid'):
                raise UserError(self.env._('Payments can only be made against an approved voucher (this one is %s).', voucher.state))
            if vals['amount'] <= 0 or vals['amount'] > voucher.remaining_amount + 0.005:
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
