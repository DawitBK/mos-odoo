from odoo import fields, models, tools


class MosVatRegister(models.Model):
    """Input VAT on supplier invoices and output VAT on customer invoices, one row per invoice, for the tax return (FR-AP-017)."""

    _name = 'mos.vat.register'
    _description = 'MOS VAT register'
    _auto = False
    _order = 'date desc, id'

    kind = fields.Selection([('input', 'Input VAT (purchases)'), ('output', 'Output VAT (sales)')], readonly=True)
    date = fields.Date(readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    tin = fields.Char(string='TIN', readonly=True)
    document = fields.Char(readonly=True)
    reference = fields.Char(readonly=True)
    net_amount = fields.Float(readonly=True, aggregator='sum')
    vat_amount = fields.Float(readonly=True, aggregator='sum')
    self_billed = fields.Boolean(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT i.id AS id, 'input' AS kind, i.invoice_date AS date, i.supplier_id AS partner_id, p.vat AS tin,
                       i.name AS document, i.supplier_ref AS reference, i.net_amount, i.vat_amount,
                       COALESCE(i.self_billed, FALSE) AS self_billed
                  FROM mos_supplier_invoice i JOIN res_partner p ON p.id = i.supplier_id
                 WHERE i.state NOT IN ('draft', 'cancelled')
                UNION ALL
                SELECT 1000000000 + c.id AS id, 'output' AS kind, c.date, c.customer_id AS partner_id, p.vat AS tin,
                       c.name AS document, c.fiscal_receipt_ref AS reference, c.net_amount, c.vat_amount, FALSE AS self_billed
                  FROM mos_customer_invoice c JOIN res_partner p ON p.id = c.customer_id
            )
            """
        )


class MosWhtRegister(models.Model):
    """Tax withheld from supplier payments, by certificate; voided certificates are listed and excluded from the totals (FR-AP-017)."""

    _name = 'mos.wht.register'
    _description = 'MOS withholding register'
    _auto = False
    _order = 'date desc, id'

    date = fields.Date(readonly=True)
    certificate = fields.Char(readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True)
    tin = fields.Char(string='TIN', readonly=True)
    payment = fields.Char(readonly=True)
    base_amount = fields.Float(readonly=True, aggregator='sum')
    wht_amount = fields.Float(string='Withheld', readonly=True, aggregator='sum')
    state = fields.Selection([('issued', 'Issued'), ('void', 'Void')], readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT c.id AS id, pay.date AS date, c.name AS certificate, c.supplier_id AS partner_id, p.vat AS tin,
                       pay.name AS payment,
                       CASE WHEN c.state = 'void' THEN 0 ELSE c.base_amount END AS base_amount,
                       CASE WHEN c.state = 'void' THEN 0 ELSE c.wht_amount END AS wht_amount,
                       c.state
                  FROM mos_wht_certificate c
                  JOIN mos_payment pay ON pay.id = c.payment_id
                  JOIN res_partner p ON p.id = c.supplier_id
            )
            """
        )
