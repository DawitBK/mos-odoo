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


class MosStockBalance(models.TransientModel):
    """Opening stock, receipts, issues and closing stock for each product between two dates, in the product's own unit (FR-INV-014, FR-RPT-004)."""

    _name = 'mos.stock.balance'
    _description = 'MOS stock balance report'

    date_from = fields.Date(required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    line_ids = fields.One2many('mos.stock.balance.line', 'balance_id')

    def action_build(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            from odoo.exceptions import UserError

            raise UserError(self.env._('The end date is before the start date.'))
        self.env.cr.execute(
            """
            WITH flow AS (
                SELECT sm.product_id, sm.date::date AS day,
                       CASE WHEN src.usage <> 'internal' AND dst.usage = 'internal' THEN sm.product_qty
                            WHEN src.usage = 'internal' AND dst.usage <> 'internal' THEN -sm.product_qty
                            ELSE 0 END AS net
                  FROM stock_move sm
                  JOIN stock_location src ON src.id = sm.location_id
                  JOIN stock_location dst ON dst.id = sm.location_dest_id
                 WHERE sm.state = 'done' AND sm.date::date <= %(to)s
            )
            SELECT product_id,
                   COALESCE(SUM(net) FILTER (WHERE day < %(from)s), 0),
                   COALESCE(SUM(net) FILTER (WHERE day >= %(from)s AND net > 0), 0),
                   COALESCE(-SUM(net) FILTER (WHERE day >= %(from)s AND net < 0), 0)
              FROM flow GROUP BY product_id
            """,
            {'from': self.date_from, 'to': self.date_to},
        )
        rows = [
            {'balance_id': self.id, 'product_id': pid, 'opening': round(op, 3), 'received': round(rec, 3), 'issued': round(iss, 3), 'closing': round(op + rec - iss, 3)}
            for pid, op, rec, iss in self.env.cr.fetchall()
            if op or rec or iss
        ]
        self.line_ids.unlink()
        self.env['mos.stock.balance.line'].create(rows)
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class MosStockBalanceLine(models.TransientModel):
    _name = 'mos.stock.balance.line'
    _description = 'MOS stock balance line'
    _order = 'product_id'

    balance_id = fields.Many2one('mos.stock.balance', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product')
    uom_id = fields.Many2one(related='product_id.uom_id')
    opening = fields.Float(digits=(14, 3))
    received = fields.Float(digits=(14, 3))
    issued = fields.Float(digits=(14, 3))
    closing = fields.Float(digits=(14, 3))
