from odoo import api, fields, models
from odoo.exceptions import UserError


class MosDeliveryOrder(models.Model):
    """A delivery to a customer: RELEASED creates the truck's expected visit at the gate; the weighbridge fixes what was
    loaded; DISPATCHED books the stock out at that weight and raises the invoice (FR-SAL-006, 015)."""

    _name = 'mos.delivery.order'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS delivery order'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    sale_id = fields.Many2one('sale.order', required=True, ondelete='restrict')
    partner_id = fields.Many2one(related='sale_id.partner_id', store=True)
    site_id = fields.Many2one('mos.site', required=True, default=lambda s: s.env.user.mos_site_ids[:1])
    picking_id = fields.Many2one('stock.picking', compute='_compute_picking', store=True)
    product_id = fields.Many2one('product.product', compute='_compute_picking', store=True)
    ordered_qty = fields.Float(compute='_compute_picking', store=True, digits=(14, 1))
    plate = fields.Char()
    driver_name = fields.Char()
    visit_id = fields.Many2one('mos.gate.visit', readonly=True, copy=False)
    loaded_qty = fields.Float(related='visit_id.net_weight', string='Loaded (kg)', digits=(14, 1))
    invoice_id = fields.Many2one('mos.customer.invoice', readonly=True, copy=False)
    state = fields.Selection(
        [('draft', 'Draft'), ('released', 'Released to the gate'), ('dispatched', 'Dispatched'), ('cancelled', 'Cancelled')],
        default='draft',
        readonly=True,
        tracking=True,
    )

    @api.depends('sale_id.picking_ids.state', 'sale_id.picking_ids.move_ids')
    def _compute_picking(self):
        for d in self:
            picking = d.sale_id.sudo().picking_ids.filtered(lambda p: p.picking_type_code == 'outgoing' and p.state not in ('done', 'cancel'))[:1]
            move = picking.move_ids[:1]
            d.picking_id = picking
            d.product_id = move.product_id
            d.ordered_qty = move.product_uom_qty

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.delivery.order')
        return super().create(vals_list)

    def action_release(self):
        for d in self:
            if d.state != 'draft':
                raise UserError(self.env._('Only a draft delivery order can be released.'))
            if d.sale_id.state != 'sale':
                raise UserError(self.env._('The sales order must be confirmed first.'))
            if not d.picking_id:
                raise UserError(self.env._('There is nothing left to deliver on this order.'))
            if not d.plate:
                raise UserError(self.env._('Enter the truck number so the gate can expect it.'))
            visit = self.env['mos.gate.visit'].sudo().create(
                {
                    'site_id': d.site_id.id,
                    'purpose': 'outbound_do',
                    'plate': d.plate,
                    'driver_name': d.driver_name,
                    'partner_id': d.partner_id.id,
                    'state': 'expected',
                    'delivery_order_id': d.id,
                }
            )
            d.sudo().write({'visit_id': visit.id, 'state': 'released'})
            self.env['mos.audit.event'].record('delivery_released', d, f'{d.name}: {d.plate}')

    def action_dispatch(self):
        """The truck is loaded and its weight posted: stock leaves at that weight and the customer is invoiced."""
        tolerance = self.env['ir.config_parameter'].sudo().get_float('mos_sales.over_delivery_pct', 2.0)
        for d in self:
            if d.state != 'released':
                raise UserError(self.env._('Only a released delivery can be dispatched.'))
            ticket = d.visit_id.ticket_id
            if not ticket or ticket.state != 'posted':
                raise UserError(self.env._('The weigh ticket of the loaded truck must be posted first.'))
            qty = ticket.net_weight
            if qty > d.ordered_qty * (1 + tolerance / 100.0) + 0.0001:
                raise UserError(self.env._('The truck carries %(qty)s kg but only %(ordered)s kg was ordered (tolerance %(tol)s%%).', qty=qty, ordered=d.ordered_qty, tol=tolerance))
            picking = d.picking_id.sudo()
            move = picking.move_ids[:1]
            if move.location_id.usage == 'internal':
                available = self.env['stock.quant'].sudo()._get_available_quantity(move.product_id, move.location_id)
                if available + 0.0001 < qty:
                    raise UserError(self.env._('Only %(have)s kg of %(product)s is in stock; the truck carries %(qty)s kg.', have=available, product=move.product_id.display_name, qty=qty))
            move.write({'quantity': qty, 'picked': True})
            result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._('The delivery needs attention in Inventory before it can be dispatched.'))
            line = d.sale_id.order_line.filtered(lambda l: l.product_id == move.product_id)[:1]
            invoice = self.env['mos.customer.invoice'].sudo().create(
                {
                    'customer_id': d.partner_id.commercial_partner_id.id,
                    'sale_id': d.sale_id.id,
                    'delivery_id': d.id,
                    'product_id': move.product_id.id,
                    'qty': qty,
                    'unit_price': line.price_unit,
                    'vat_pct': self.env['ir.config_parameter'].sudo().get_float('mos_sales.vat_pct', 15.0),
                }
            )
            d.sudo().write({'invoice_id': invoice.id, 'state': 'dispatched'})
            self.env['mos.audit.event'].record('delivery_dispatched', d, f'{d.name}: {qty} kg, invoice {invoice.name}')

    def action_cancel(self):
        for d in self:
            if d.state in ('dispatched', 'cancelled'):
                raise UserError(self.env._('A dispatched delivery cannot be cancelled.'))
            if d.visit_id and d.visit_id.ticket_id.state == 'posted':
                raise UserError(self.env._('The truck has already been weighed; reverse the weighing first.'))
            d.visit_id.sudo().write({'state': 'cancelled'}) if d.visit_id else None
            d.sudo().write({'state': 'cancelled'})
