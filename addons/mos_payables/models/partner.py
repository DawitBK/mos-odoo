from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_vat_pct = fields.Float(string='VAT on purchases (%)', default=15.0, help='Charged on this supplier\'s invoices; zero for a farmer or exempt trader.')
    mos_wht_pct = fields.Float(string='Withholding (%)', default=2.0, help='Withheld from every payment to this supplier and paid to the tax authority.')
    mos_payment_terms_days = fields.Integer(string='Payment terms (days)', default=30, help='How many days after the invoice date this supplier expects payment.')
    mos_bank_account = fields.Char(string='Payment account', help='Where payments by transfer are sent.')

    def _mos_effective_wht_pct(self):
        """The withholding rate that applies to this supplier now (other modules add rules, such as a rate for no tax number)."""
        if not self:
            return 0.0
        self.ensure_one()
        return self.mos_wht_pct
