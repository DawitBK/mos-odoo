from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_vat_pct = fields.Float(string='VAT on purchases (%)', default=15.0, help='Charged on this supplier\'s invoices; zero for a farmer or exempt trader.')
    mos_wht_pct = fields.Float(string='Withholding (%)', default=2.0, help='Withheld from every payment to this supplier and paid to the tax authority.')
    mos_bank_account = fields.Char(string='Payment account', help='Where payments by transfer are sent.')
