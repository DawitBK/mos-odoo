from odoo import fields, models, tools


class MosSupplierBalance(models.Model):
    """What we owe each supplier, aged by invoice date: a statement and an ageing in one view (FR-AP-009).

    Owed = payable amount of every invoice not yet paid, less what the payments allocated to that invoice.
    Built in the database from the ledger tables, so it can never disagree with them."""

    _name = 'mos.supplier.balance'
    _description = 'MOS supplier balance and ageing'
    _auto = False
    _order = 'balance desc'

    supplier_id = fields.Many2one('res.partner', readonly=True)
    invoices = fields.Integer(readonly=True)
    billed = fields.Float(readonly=True, string='Billed (payable)')
    paid = fields.Float(readonly=True)
    balance = fields.Float(readonly=True, string='Owed')
    current = fields.Float(readonly=True, string='0-30 days')
    days_31_60 = fields.Float(readonly=True, string='31-60 days')
    days_61_90 = fields.Float(readonly=True, string='61-90 days')
    over_90 = fields.Float(readonly=True, string='Over 90 days')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
            CREATE VIEW mos_supplier_balance AS
            WITH inv AS (
                SELECT i.id, i.supplier_id, i.payable_amount AS amount, i.invoice_date,
                       COALESCE((SELECT sum(a.amount)
                                   FROM mos_payment_line a JOIN mos_payment_voucher_line l ON l.id = a.voucher_line_id
                                  WHERE l.invoice_id = i.id %s), 0) AS paid
                  FROM mos_supplier_invoice i
                 WHERE i.state NOT IN ('draft', 'cancelled')
            ), aged AS (
                SELECT supplier_id, amount, paid, amount - paid AS owed,
                       (CURRENT_DATE - invoice_date) AS age
                  FROM inv
            )
            SELECT min(id) AS id, supplier_id, count(*) AS invoices,
                   sum(amount) AS billed, sum(paid) AS paid, sum(owed) AS balance,
                   sum(owed) FILTER (WHERE age <= 30) AS current,
                   sum(owed) FILTER (WHERE age BETWEEN 31 AND 60) AS days_31_60,
                   sum(owed) FILTER (WHERE age BETWEEN 61 AND 90) AS days_61_90,
                   sum(owed) FILTER (WHERE age > 90) AS over_90
              FROM (SELECT row_number() OVER () AS id, * FROM aged) a
             GROUP BY supplier_id
            """
            % self._mos_allocation_filter()
        )

    def _mos_allocation_filter(self):
        """Extra SQL condition on the payment allocations counted as paid (alias `a`); later modules exclude what no longer counts, such as reversed payments."""
        return ''

