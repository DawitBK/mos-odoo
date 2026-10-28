from odoo import api, models
from odoo.exceptions import UserError


class MosPaymentDepositSlip(models.Model):
    """With ``mos_documents.require_deposit_slip`` on, a bank transfer is recorded only when the voucher carries its deposit slip (the
    client's payment screen requires one). Off by default."""

    _inherit = 'mos.payment'

    @api.model_create_multi
    def create(self, vals_list):
        if self.env['ir.config_parameter'].sudo().get_bool('mos_documents.require_deposit_slip'):
            for vals in vals_list:
                if vals.get('method', 'transfer') == 'transfer' and vals.get('voucher_id'):
                    voucher = self.env['mos.payment.voucher'].browse(vals['voucher_id'])
                    if not voucher._mos_has_attached('deposit_slip'):
                        raise UserError(self.env._('Attach the deposit slip to %s before recording a bank transfer.', voucher.display_name))
        return super().create(vals_list)
