from odoo import api, fields, models


class MosReservation(models.Model):
    """Stock set aside for a confirmed sales order, so the same flour is not promised twice. A soft reservation: it reduces what
    other orders see as available-to-use but moves no stock. It shrinks as the order is delivered and ends when the order is
    cancelled or fully delivered (FR-SAL-005)."""

    _name = 'mos.reservation'
    _description = 'MOS stock reservation'
    _order = 'id desc'

    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', index=True)
    line_id = fields.Many2one('sale.order.line', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, index=True)
    qty = fields.Float(string='Reserved', required=True, digits=(14, 1))
    remaining = fields.Float(compute='_compute_remaining', digits=(14, 1), string='Still held')
    state = fields.Selection([('active', 'Active'), ('released', 'Released')], default='active', required=True, index=True)

    @api.depends('qty', 'line_id.qty_delivered', 'state')
    def _compute_remaining(self):
        for res in self:
            res.remaining = max(res.qty - res.line_id.qty_delivered, 0.0) if res.state == 'active' else 0.0

    def _refresh_products(self):
        self.env['product.product'].invalidate_model(['mos_reserved_qty', 'mos_free_qty'])

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._refresh_products()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._refresh_products()
        return result

    def action_release(self):
        self.write({'state': 'released'})

    @api.model
    def _cron_release(self):
        """End reservations whose order is cancelled, or whose goods have all been delivered."""
        active = self.search([('state', '=', 'active')])
        done = active.filtered(lambda r: r.order_id.state not in ('sale',) or r.line_id.qty_delivered >= r.qty - 0.0001)
        done.write({'state': 'released'})
        return len(done)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    mos_reserved_qty = fields.Float(compute='_compute_mos_reserved', string='Reserved for orders')
    mos_free_qty = fields.Float(compute='_compute_mos_reserved', string='Available to use')

    def _compute_mos_reserved(self):
        for product in self:
            quants = self.env['stock.quant'].sudo().search([('product_id', '=', product.id), ('location_id.usage', '=', 'internal'), ('location_id.mos_stock_status', '=', 'available')])
            reserved = sum(self.env['mos.reservation'].sudo().search([('product_id', '=', product.id), ('state', '=', 'active')]).mapped('remaining'))
            product.mos_reserved_qty = reserved
            product.mos_free_qty = max(sum(quants.mapped('quantity')) - reserved, 0.0)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    mos_reservation_ids = fields.One2many('mos.reservation', 'order_id')

    def action_confirm(self):
        result = super().action_confirm()
        for order in self.filtered(lambda o: o.state == 'sale'):
            order._mos_reserve()
        return result

    def _mos_reserve(self):
        """Reserve what is available-to-use for each stock line; say plainly when the order asks for more."""
        self.ensure_one()
        shortfalls = []
        for line in self.order_line.filtered(lambda l: l.product_id and l.product_id.is_storable and not l.display_type):
            if self.env['mos.reservation'].sudo().search_count([('line_id', '=', line.id), ('state', '=', 'active')]):
                continue
            product = line.product_id
            free = product.mos_free_qty
            take = min(free, line.product_uom_qty)
            if take > 0:
                self.env['mos.reservation'].sudo().create({'order_id': self.id, 'line_id': line.id, 'product_id': product.id, 'qty': take})
            if take < line.product_uom_qty - 0.0001:
                shortfalls.append(self.env._('%(product)s: asked for %(asked)s, reserved %(got)s', product=product.display_name, asked=line.product_uom_qty, got=take))
        if shortfalls:
            self.message_post(body=self.env._('Not enough available-to-use stock to cover this order: %s', '; '.join(shortfalls)))
        return not shortfalls

    def _action_cancel(self):
        result = super()._action_cancel()
        self.mos_reservation_ids.sudo().filtered(lambda r: r.state == 'active').write({'state': 'released'})
        return result
