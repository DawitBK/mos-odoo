from odoo import api, fields, models
from odoo.exceptions import UserError


class MosPaymentDepositSlip(models.Model):
    """The deposit slip of a bank transfer, uploaded on the payment screen and kept with the voucher it pays (client screen 25).

    With ``mos_documents.require_deposit_slip`` on, a bank transfer is recorded only when the voucher carries its deposit slip, either
    uploaded earlier on the voucher or with the payment. Off by default."""

    _inherit = 'mos.payment'

    deposit_slip = fields.Binary(string='Deposit slip', store=False, help='Only used while making the payment: the file is kept as an attachment of the voucher.')
    deposit_slip_name = fields.Char(store=False)
    slip_ids = fields.Many2many('mos.attachment', compute='_compute_slip_ids', string='Deposit slips')
    slip_hint = fields.Char(compute='_compute_slip_hint')

    @api.depends('voucher_id')
    def _compute_slip_ids(self):
        for pay in self:
            pay.slip_ids = pay.voucher_id.sudo().mos_attachment_ids.filtered(lambda a: a.kind == 'deposit_slip')

    def _compute_slip_hint(self):
        limit = self.env['ir.config_parameter'].sudo().get_int('mos_core.attachment_max_mb', 10)
        for pay in self:
            pay.slip_hint = self.env._('PDF, JPG, PNG (max %s MB)', limit)

    @api.model_create_multi
    def create(self, vals_list):
        slips = []
        for vals in vals_list:
            slip, name = vals.pop('deposit_slip', False), vals.pop('deposit_slip_name', False)
            if isinstance(slip, dict):  # the web client sends an uploaded file as {filename, content}
                name, slip = slip.get('filename') or name, slip.get('content')
            slips.append((slip, name))
        if self.env['ir.config_parameter'].sudo().get_bool('mos_documents.require_deposit_slip'):
            for vals, (slip, _name) in zip(vals_list, slips):
                if vals.get('method', 'transfer') == 'transfer' and vals.get('voucher_id') and not slip:
                    voucher = self.env['mos.payment.voucher'].browse(vals['voucher_id'])
                    if not voucher._mos_has_attached('deposit_slip'):
                        raise UserError(self.env._('Attach the deposit slip to %s before recording a bank transfer.', voucher.display_name))
        for vals, (slip, name) in zip(vals_list, slips):
            if slip and vals.get('voucher_id'):
                self.env['mos.attachment'].create(
                    {'name': name or self.env._('deposit_slip.pdf'), 'file': slip, 'res_model': 'mos.payment.voucher', 'res_id': vals['voucher_id'], 'kind': 'deposit_slip'}
                )
        return super().create(vals_list)
