from odoo import api, fields, models
from odoo.exceptions import UserError


class MosPaymentRun(models.Model):
    """Pay every approved voucher that is due in one go: the system proposes, the cashier crosses out what should wait, and one
    action records all the payments. Each payment still obeys its own rules (approved voucher, segregation of duties) (FR-AP-011)."""

    _name = 'mos.payment.run'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS payment run'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    date = fields.Date(default=fields.Date.context_today, required=True)
    account_id = fields.Many2one('mos.paying.account', required=True)
    method = fields.Selection([('transfer', 'Bank transfer'), ('cheque', 'Cheque'), ('cash', 'Cash'), ('wallet', 'Mobile money')], default='transfer', required=True)
    cheque_book_id = fields.Many2one('mos.cheque.book', domain="[('account_id', '=', account_id)]")
    line_ids = fields.One2many('mos.payment.run.line', 'run_id')
    total = fields.Float(compute='_compute_total', store=True)
    state = fields.Selection([('draft', 'Draft'), ('executed', 'Executed'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)

    @api.depends('line_ids.amount', 'line_ids.include')
    def _compute_total(self):
        for run in self:
            run.total = sum(run.line_ids.filtered('include').mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.payment.run')
        return super().create(vals_list)

    def action_propose(self):
        for run in self:
            if run.state != 'draft':
                raise UserError(self.env._('Only a draft run can be proposed again.'))
            run.line_ids.unlink()
            vouchers = self.env['mos.payment.voucher'].sudo().search([('state', 'in', ('approved', 'partially_paid'))], order='id')
            lines = [
                {'run_id': run.id, 'voucher_id': v.id, 'amount': v.remaining_amount}
                for v in vouchers
                if v.remaining_amount > 0.005 and v.supplier_id.mos_supplier_state != 'blocked'
            ]
            if not lines:
                raise UserError(self.env._('No approved voucher is waiting for payment.'))
            self.env['mos.payment.run.line'].create(lines)

    def action_execute(self):
        for run in self:
            if run.state != 'draft':
                raise UserError(self.env._('This run is already closed.'))
            chosen = run.line_ids.filtered('include')
            if not chosen:
                raise UserError(self.env._('Nothing is selected to pay.'))
            if run.method == 'cheque' and not run.cheque_book_id:
                raise UserError(self.env._('Choose the cheque book for a cheque run.'))
            payments = self.env['mos.payment']
            for line in chosen:
                vals = {'voucher_id': line.voucher_id.id, 'amount': min(line.amount, line.voucher_id.remaining_amount), 'account_id': run.account_id.id, 'method': run.method, 'date': run.date, 'reference': run.name}
                if run.method == 'cheque':
                    vals['cheque_book_id'] = run.cheque_book_id.id
                try:
                    with self.env.cr.savepoint():
                        payments |= self.env['mos.payment'].create(vals)
                except UserError as error:
                    raise UserError(self.env._('The run stopped at voucher %(v)s: %(why)s Nothing was paid.', v=line.voucher_id.name, why=error.args[0]))
            for line, payment in zip(chosen, payments):
                line.payment_id = payment
            run.write({'state': 'executed'})
            self.env['mos.audit.event'].record('payment_run_executed', run, f'{run.name}: {len(payments)} payments, {run.total:.2f}')

    def action_cancel(self):
        for run in self:
            if run.state != 'draft':
                raise UserError(self.env._('Only a draft run can be cancelled.'))
            run.state = 'cancelled'


class MosPaymentRunLine(models.Model):
    _name = 'mos.payment.run.line'
    _description = 'MOS payment run line'
    _order = 'id'

    run_id = fields.Many2one('mos.payment.run', required=True, ondelete='cascade')
    voucher_id = fields.Many2one('mos.payment.voucher', required=True)
    supplier_id = fields.Many2one(related='voucher_id.supplier_id')
    amount = fields.Float(required=True)
    include = fields.Boolean(default=True)
    payment_id = fields.Many2one('mos.payment', readonly=True)
