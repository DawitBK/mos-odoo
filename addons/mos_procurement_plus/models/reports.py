from odoo import fields, models, tools


class MosPoOutstanding(models.Model):
    """What suppliers still owe us: every open order line with its delivery date and how late it is (FR-PRC-017)."""

    _name = 'mos.po.outstanding'
    _description = 'MOS outstanding deliveries'
    _auto = False
    _order = 'days_late desc'

    order_id = fields.Many2one('purchase.order', readonly=True)
    supplier_id = fields.Many2one('res.partner', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    ordered = fields.Float(readonly=True, aggregator='sum')
    received = fields.Float(readonly=True, aggregator='sum')
    outstanding = fields.Float(readonly=True, aggregator='sum')
    planned_date = fields.Date(readonly=True)
    days_late = fields.Integer(readonly=True, aggregator='max')
    value_outstanding = fields.Float(readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT pol.id AS id, po.id AS order_id, po.partner_id AS supplier_id, pol.product_id,
                       pol.product_qty AS ordered, COALESCE(pol.qty_received, 0) AS received,
                       GREATEST(pol.product_qty - COALESCE(pol.qty_received, 0), 0) AS outstanding,
                       pol.date_planned::date AS planned_date,
                       GREATEST((CURRENT_DATE - pol.date_planned::date), 0) AS days_late,
                       GREATEST(pol.product_qty - COALESCE(pol.qty_received, 0), 0) * pol.price_unit AS value_outstanding
                  FROM purchase_order_line pol
                  JOIN purchase_order po ON po.id = pol.order_id
                 WHERE po.state = 'purchase' AND pol.product_qty > COALESCE(pol.qty_received, 0)
                   AND COALESCE(po.mos_close_state, 'open') = 'open'
            )
            """
        )


class MosPrAgeing(models.Model):
    """How long purchase requests have been waiting, by stage (FR-PRC-017)."""

    _name = 'mos.pr.ageing'
    _description = 'MOS purchase request ageing'
    _auto = False
    _order = 'age_days desc'

    request_id = fields.Many2one('mos.purchase.request', readonly=True)
    site_id = fields.Many2one('mos.site', readonly=True)
    requester_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('converted', 'Ordered'), ('cancelled', 'Cancelled')], readonly=True
    )
    amount = fields.Float(readonly=True, aggregator='sum')
    age_days = fields.Integer(readonly=True, aggregator='avg')
    count = fields.Integer(readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT r.id AS id, r.id AS request_id, r.site_id, r.requester_id, r.state, r.amount,
                       (CURRENT_DATE - r.create_date::date) AS age_days, 1 AS count
                  FROM mos_purchase_request r
            )
            """
        )


class MosPriceHistory(models.Model):
    """What each product cost from each supplier over time, from the confirmed orders (FR-PRC-017)."""

    _name = 'mos.price.history'
    _description = 'MOS price history'
    _auto = False
    _order = 'date desc'

    product_id = fields.Many2one('product.product', readonly=True)
    supplier_id = fields.Many2one('res.partner', readonly=True)
    order_id = fields.Many2one('purchase.order', readonly=True)
    date = fields.Date(readonly=True)
    qty = fields.Float(readonly=True, aggregator='sum')
    price = fields.Float(readonly=True, aggregator='avg', digits=(12, 4))

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT pol.id AS id, pol.product_id, po.partner_id AS supplier_id, po.id AS order_id,
                       COALESCE(po.date_approve, po.date_order)::date AS date, pol.product_qty AS qty, pol.price_unit AS price
                  FROM purchase_order_line pol JOIN purchase_order po ON po.id = pol.order_id
                 WHERE po.state IN ('purchase', 'done') AND pol.product_id IS NOT NULL
            )
            """
        )
