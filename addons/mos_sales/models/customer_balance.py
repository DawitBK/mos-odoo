from odoo import fields, models, tools


class MosCustomerBalance(models.Model):
    """What each customer owes, aged by invoice date, net of advances (FR-SAL-010). Built in the database from the
    invoices and receipts, so it cannot drift from them."""

    _name = 'mos.customer.balance'
    _description = 'MOS customer balance and ageing'
    _auto = False
    _order = 'balance desc'

    customer_id = fields.Many2one('res.partner', readonly=True)
    invoiced = fields.Float(readonly=True)
    received = fields.Float(readonly=True, string='Received (incl. advances)')
    balance = fields.Float(readonly=True, string='Owes us')
    current = fields.Float(readonly=True, string='0-30 days')
    days_31_60 = fields.Float(readonly=True, string='31-60 days')
    days_61_90 = fields.Float(readonly=True, string='61-90 days')
    over_90 = fields.Float(readonly=True, string='Over 90 days')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
            CREATE VIEW mos_customer_balance AS
            WITH inv AS (
                SELECT customer_id, balance AS open_amount, (CURRENT_DATE - date) AS age, gross_amount AS gross
                  FROM mos_customer_invoice
            ), adv AS (
                SELECT customer_id, sum(amount) AS advances FROM mos_receipt WHERE invoice_id IS NULL GROUP BY customer_id
            ), per AS (
                SELECT customer_id,
                       sum(gross) AS invoiced,
                       sum(open_amount) AS open_total,
                       sum(open_amount) FILTER (WHERE age <= 30) AS c0,
                       sum(open_amount) FILTER (WHERE age BETWEEN 31 AND 60) AS c31,
                       sum(open_amount) FILTER (WHERE age BETWEEN 61 AND 90) AS c61,
                       sum(open_amount) FILTER (WHERE age > 90) AS c90
                  FROM inv GROUP BY customer_id
            )
            SELECT row_number() OVER () AS id, COALESCE(per.customer_id, adv.customer_id) AS customer_id,
                   COALESCE(per.invoiced, 0) AS invoiced,
                   COALESCE(per.invoiced, 0) - COALESCE(per.open_total, 0) + COALESCE(adv.advances, 0) AS received,
                   COALESCE(per.open_total, 0) - COALESCE(adv.advances, 0) AS balance,
                   COALESCE(per.c0, 0) AS current, COALESCE(per.c31, 0) AS days_31_60,
                   COALESCE(per.c61, 0) AS days_61_90, COALESCE(per.c90, 0) AS over_90
              FROM per FULL JOIN adv ON adv.customer_id = per.customer_id
            """
        )
