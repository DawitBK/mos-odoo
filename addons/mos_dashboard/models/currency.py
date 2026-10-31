from odoo import api, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _mos_keep_birr(self):
        """A new MOS database belongs to an Ethiopian mill: its money is birr (checked at install and after the chart of accounts loads).
        Only while the books are empty: a company that already has entries never has its currency changed under them."""
        birr = self.env.ref('base.ETB')
        birr.active = True
        company = self.env.ref('base.main_company')
        if company.currency_id == birr:
            return False
        if self.env['account.move'].sudo().search_count([('company_id', '=', company.id), ('state', '!=', 'draft')]):
            return False
        company.currency_id = birr
        self.env['product.pricelist'].with_context(active_test=False).sudo().search([]).write({'currency_id': birr.id})
        return True


class AccountChartTemplate(models.AbstractModel):
    _inherit = 'account.chart.template'

    def _load(self, template_code, company, install_demo, force_create=True):
        # Odoo's generic chart of accounts sets the company's money to US dollars when it loads, after the addons are installed.
        result = super()._load(template_code, company, install_demo, force_create=force_create)
        if company == self.env.ref('base.main_company'):
            self.env['res.company']._mos_keep_birr()
        return result
