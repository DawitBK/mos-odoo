from odoo import fields, models, tools
from odoo.exceptions import UserError


class MosStockHistory(models.Model):
    """Every finished stock movement with what came in, what went out, what was adjusted and the balance after it, per product
    (the transaction history behind the stock balance, FR-INV-014)."""

    _name = 'mos.stock.history'
    _description = 'MOS stock transaction history'
    _auto = False
    _order = 'product_id, date, id'

    product_id = fields.Many2one('product.product', readonly=True)
    date = fields.Datetime(readonly=True)
    move_id = fields.Many2one('stock.move', readonly=True)
    reference = fields.Char(readonly=True)
    kind = fields.Selection([('received', 'Received'), ('issued', 'Issued'), ('adjusted', 'Adjusted')], readonly=True)
    received = fields.Float(readonly=True, aggregator='sum')
    issued = fields.Float(readonly=True, aggregator='sum')
    adjustment = fields.Float(readonly=True, aggregator='sum')
    balance_after = fields.Float(readonly=True, aggregator=None)
    user_id = fields.Many2one('res.users', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH flow AS (
                    SELECT sm.id, sm.product_id, sm.date, sm.reference, sm.write_uid AS user_id,
                           CASE WHEN src.usage = 'inventory' OR dst.usage = 'inventory' THEN 'adjusted'
                                WHEN src.usage <> 'internal' AND dst.usage = 'internal' THEN 'received'
                                WHEN src.usage = 'internal' AND dst.usage <> 'internal' THEN 'issued'
                           END AS kind,
                           CASE WHEN src.usage <> 'internal' AND dst.usage = 'internal' THEN sm.product_qty
                                WHEN src.usage = 'internal' AND dst.usage <> 'internal' THEN -sm.product_qty
                                ELSE 0 END AS net
                      FROM stock_move sm
                      JOIN stock_location src ON src.id = sm.location_id
                      JOIN stock_location dst ON dst.id = sm.location_dest_id
                     WHERE sm.state = 'done' AND (src.usage = 'internal') <> (dst.usage = 'internal')
                )
                SELECT f.id AS id, f.id AS move_id, f.product_id, f.date, f.reference, f.kind, f.user_id,
                       CASE WHEN f.kind = 'received' THEN f.net ELSE 0 END AS received,
                       CASE WHEN f.kind = 'issued' THEN -f.net ELSE 0 END AS issued,
                       CASE WHEN f.kind = 'adjusted' THEN f.net ELSE 0 END AS adjustment,
                       SUM(f.net) OVER (PARTITION BY f.product_id ORDER BY f.date, f.id) AS balance_after
                  FROM flow f
            )
            """
        )


class MosStockBalance(models.TransientModel):
    """Opening stock, received, issued, adjusted and closing stock for each product between two dates, in the product's own unit,
    with the status breakdown as it stands now (FR-INV-014, FR-RPT-004). Closing = opening + received - issued + adjusted."""

    _name = 'mos.stock.balance'
    _description = 'MOS stock balance report'

    date_from = fields.Date(required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    line_ids = fields.One2many('mos.stock.balance.line', 'balance_id')

    def action_build(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._('The end date is before the start date.'))
        self.env.flush_all()
        self.env.cr.execute(
            """
            WITH flow AS (
                SELECT sm.product_id, sm.date::date AS day,
                       (src.usage = 'inventory' OR dst.usage = 'inventory') AS adj,
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
                   COALESCE(SUM(net) FILTER (WHERE day >= %(from)s AND net > 0 AND NOT adj), 0),
                   COALESCE(-SUM(net) FILTER (WHERE day >= %(from)s AND net < 0 AND NOT adj), 0),
                   COALESCE(SUM(net) FILTER (WHERE day >= %(from)s AND adj), 0)
              FROM flow GROUP BY product_id
            """,
            {'from': self.date_from, 'to': self.date_to},
        )
        movements = self.env.cr.fetchall()
        status = {}
        if self.date_to >= fields.Date.context_today(self):
            for quant in self.env['stock.quant'].sudo().search([('location_id.usage', '=', 'internal')]):
                bucket = status.setdefault(quant.product_id.id, {'available': 0.0, 'quarantine': 0.0, 'blocked': 0.0, 'reserved': 0.0})
                bucket[quant.location_id.mos_stock_status] += quant.quantity
                if quant.location_id.mos_stock_status == 'available':
                    bucket['reserved'] += quant.reserved_quantity
        rows = []
        for pid, op, rec, iss, adj in movements:
            if not (op or rec or iss or adj):
                continue
            s = status.get(pid, {})
            rows.append(
                {
                    'balance_id': self.id,
                    'product_id': pid,
                    'opening': round(op, 3),
                    'received': round(rec, 3),
                    'issued': round(iss, 3),
                    'adjusted': round(adj, 3),
                    'closing': round(op + rec - iss + adj, 3),
                    'available': round(s.get('available', 0.0), 3),
                    'reserved': round(s.get('reserved', 0.0), 3),
                    'quarantine': round(s.get('quarantine', 0.0), 3),
                    'blocked': round(s.get('blocked', 0.0), 3),
                }
            )
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
    adjusted = fields.Float(digits=(14, 3))
    closing = fields.Float(digits=(14, 3))
    available = fields.Float(digits=(14, 3), help='As it stands now; empty for a report that ends in the past.')
    reserved = fields.Float(digits=(14, 3), string='of which reserved')
    quarantine = fields.Float(digits=(14, 3))
    blocked = fields.Float(digits=(14, 3))

    def action_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('History of %s', self.product_id.display_name),
            'res_model': 'mos.stock.history',
            'view_mode': 'list',
            'domain': [('product_id', '=', self.product_id.id)],
        }
