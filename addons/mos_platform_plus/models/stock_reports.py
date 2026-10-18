from odoo import api, fields, models, tools


class MosStockPosition(models.Model):
    """Where every product is, by location, with its MOS status: the stock overview (FR-INV-001)."""

    _name = 'mos.stock.position'
    _description = 'MOS stock position'
    _auto = False
    _order = 'product_id, location_id'

    product_id = fields.Many2one('product.product', readonly=True)
    location_id = fields.Many2one('stock.location', readonly=True)
    status = fields.Selection([('available', 'Available'), ('quarantine', 'Quarantine'), ('blocked', 'Blocked')], readonly=True)
    on_hand = fields.Float(readonly=True, aggregator='sum')
    reserved = fields.Float(readonly=True, aggregator='sum')
    free = fields.Float(readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT MIN(sq.id) AS id, sq.product_id, sq.location_id, sl.mos_stock_status AS status,
                       SUM(sq.quantity) AS on_hand, SUM(sq.reserved_quantity) AS reserved,
                       SUM(sq.quantity - sq.reserved_quantity) AS free
                  FROM stock_quant sq
                  JOIN stock_location sl ON sl.id = sq.location_id
                 WHERE sl.usage = 'internal'
              GROUP BY sq.product_id, sq.location_id, sl.mos_stock_status
                HAVING SUM(sq.quantity) <> 0
            )
            """
        )


class MosReceiptValuation(models.Model):
    """What each received delivery was worth at the order price, so receipts can be tied to the books (FR-INV-022)."""

    _name = 'mos.receipt.valuation'
    _description = 'MOS receipt valuation'
    _auto = False
    _order = 'date desc, id desc'

    picking_id = fields.Many2one('stock.picking', readonly=True)
    order_id = fields.Many2one('purchase.order', readonly=True)
    supplier_id = fields.Many2one('res.partner', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    date = fields.Date(readonly=True)
    qty = fields.Float(string='Received', readonly=True, aggregator='sum')
    unit_price = fields.Float(string="Quality-adjusted price", readonly=True)
    payable = fields.Float(string="Payable quantity", readonly=True, aggregator="sum")
    value = fields.Float(readonly=True, aggregator="sum")
    unit_cost = fields.Float(string="Unit cost (value per physical unit)", readonly=True)
    invoiced = fields.Float(string='Invoiced quantity', readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT sm.id AS id, sm.picking_id, po.id AS order_id, po.partner_id AS supplier_id, sm.product_id,
                       sm.date::date AS date, sm.product_qty AS qty,
                       pol.price_unit * (1 + COALESCE(i.price_adjust_pct, 0) / 100.0) AS unit_price,
                       COALESCE(i.payable_qty, sm.product_qty) AS payable,
                       COALESCE(i.payable_qty, sm.product_qty) * pol.price_unit * (1 + COALESCE(i.price_adjust_pct, 0) / 100.0) AS value,
                       CASE WHEN sm.product_qty > 0 THEN COALESCE(i.payable_qty, sm.product_qty) * pol.price_unit * (1 + COALESCE(i.price_adjust_pct, 0) / 100.0) / sm.product_qty ELSE 0 END AS unit_cost,
                       COALESCE(pol.qty_invoiced, 0) AS invoiced
                  FROM stock_move sm
                  JOIN purchase_order_line pol ON pol.id = sm.purchase_line_id
                  JOIN purchase_order po ON po.id = pol.order_id
                  JOIN stock_location src ON src.id = sm.location_id
                  JOIN stock_location dst ON dst.id = sm.location_dest_id
                  LEFT JOIN mos_gate_visit gv ON gv.grn_picking_id = sm.picking_id
                  LEFT JOIN mos_lab_inspection i ON i.visit_id = gv.id AND i.product_id = sm.product_id AND i.state IN ('decided', 'closed')
                 WHERE sm.state = 'done' AND src.usage <> 'internal' AND dst.usage = 'internal'
            )
            """
        )


