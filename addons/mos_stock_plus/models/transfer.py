from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosException(models.Model):
    _inherit = 'mos.exception'

    kind = fields.Selection(selection_add=[('transit_late', 'Stock in transit too long')], ondelete={'transit_late': 'cascade'})


class MosTransferOrder(models.Model):
    """Stock sent from one site to another. At dispatch it leaves the source and sits in transit, valued at the dispatch cost; at
    receipt the other site counts what arrived and any shortfall is written off as a transit loss with a reason. The person who
    dispatches cannot be the one who receives (FR-INV-007)."""

    _name = 'mos.transfer.order'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS transfer between sites'
    _order = 'id desc'
    _mos_final_states = ('received', 'cancelled')
    _mos_editable_after_post = {'state', 'dispatched_by_id', 'dispatched_at', 'received_by_id', 'received_at', 'dispatch_picking_id', 'receipt_picking_id', 'loss_picking_id', 'loss_reason_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    from_site_id = fields.Many2one('mos.site', required=True, string='From')
    to_site_id = fields.Many2one('mos.site', required=True, string='To')
    line_ids = fields.One2many('mos.transfer.order.line', 'order_id', string='Items')
    state = fields.Selection([('draft', 'Draft'), ('dispatched', 'In transit'), ('received', 'Received'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)
    note = fields.Char()
    dispatched_by_id = fields.Many2one('res.users', readonly=True)
    dispatched_at = fields.Datetime(readonly=True)
    received_by_id = fields.Many2one('res.users', readonly=True)
    received_at = fields.Datetime(readonly=True)
    loss_reason_id = fields.Many2one('mos.reason', readonly=True, domain=[('domain', '=', 'transit_loss')])
    dispatch_picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    receipt_picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    loss_picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    days_in_transit = fields.Integer(compute='_compute_days')

    @api.depends('state', 'dispatched_at')
    def _compute_days(self):
        now = fields.Datetime.now()
        for order in self:
            order.days_in_transit = (now - order.dispatched_at).days if order.state == 'dispatched' and order.dispatched_at else 0

    @api.constrains('from_site_id', 'to_site_id')
    def _check_sites(self):
        for order in self:
            if order.from_site_id == order.to_site_id:
                raise ValidationError(self.env._('A transfer goes between two different sites; use an internal move within one site.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.transfer.order')
        return super().create(vals_list)

    # -- locations and moves --------------------------------------------------------------------------------------------
    def _transit_location(self):
        return self.env.ref('mos_stock_plus.location_mos_transit').sudo()

    def _loss_location(self):
        loc = self.env['stock.location'].sudo().search([('usage', '=', 'inventory')], limit=1)
        if not loc:
            raise UserError(self.env._('There is no inventory-loss location to write the shortfall off to.'))
        return loc

    def _move(self, src, dst, lines, site, label):
        """One internal picking for `lines` ([(line, qty)]) from src to dst, validated at once. Returns the picking."""
        picking = self.env['stock.picking'].sudo().create(
            {
                'picking_type_id': site.warehouse_id.int_type_id.id,
                'location_id': src.id,
                'location_dest_id': dst.id,
                'origin': f'{self.name} {label}',
                'move_ids': [Command.create({'product_id': line.product_id.id, 'product_uom_qty': qty, 'location_id': src.id, 'location_dest_id': dst.id}) for line, qty in lines if qty > 0],
            }
        )
        picking.action_confirm()
        for move, (line, qty) in zip(picking.move_ids, [(l, q) for l, q in lines if q > 0]):
            move.write({'quantity': qty, 'picked': True})
            if line.lot_id and move.move_line_ids:
                move.move_line_ids.write({'lot_id': line.lot_id.id})
        result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        if isinstance(result, dict):
            raise UserError(self.env._('The stock move for %s needs attention in Inventory.', self.name))
        return picking

    # -- actions -----------------------------------------------------------------------------------------------------------
    def action_dispatch(self):
        user = self.env.user
        for order in self:
            if order.state != 'draft':
                raise UserError(self.env._('Only a draft transfer can be dispatched.'))
            if not order.line_ids:
                raise UserError(self.env._('Add the items to send.'))
            if not (user.has_group('mos_inbound.group_mos_storekeeper') or user.has_group('mos_core.group_mos_admin')):
                raise UserError(self.env._('Only a storekeeper can dispatch stock.'))
            if order.from_site_id not in user.mos_site_ids and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('You do not work at %s, so you cannot dispatch from it.', order.from_site_id.display_name))
            source = order.from_site_id.warehouse_id.lot_stock_id
            for line in order.line_ids:
                have = sum(self.env['stock.quant'].sudo().search([('product_id', '=', line.product_id.id), ('location_id', 'child_of', source.id), ('location_id.mos_stock_status', '=', 'available')] + ([('lot_id', '=', line.lot_id.id)] if line.lot_id else [])).mapped('quantity'))
                if have + 0.0001 < line.qty:
                    raise UserError(self.env._('%(site)s has only %(have)s of %(product)s available to send.', site=order.from_site_id.display_name, have=have, product=line.product_id.display_name))
            picking = order._move(source, order._transit_location(), [(l, l.qty) for l in order.line_ids], order.from_site_id, 'dispatch')
            order.write({'state': 'dispatched', 'dispatched_by_id': user.id, 'dispatched_at': fields.Datetime.now(), 'dispatch_picking_id': picking.id})
            self.env['mos.audit.event'].record('transfer_dispatched', order, f'{order.name}: {order.from_site_id.sudo().code} -> {order.to_site_id.sudo().code}, {len(order.line_ids)} lines')

    def action_receive(self, received=None, reason=None):
        """Count what arrived. `received` maps a line id to the quantity counted (default: all of it); any shortfall needs a reason."""
        user = self.env.user
        received = received or {}
        for order in self:
            if order.state != 'dispatched':
                raise UserError(self.env._('Only stock in transit can be received.'))
            if not (user.has_group('mos_inbound.group_mos_storekeeper') or user.has_group('mos_core.group_mos_admin')):
                raise UserError(self.env._('Only a storekeeper can receive stock.'))
            if order.to_site_id not in user.mos_site_ids and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('You do not work at %s, so you cannot receive there.', order.to_site_id.display_name))
            if order.dispatched_by_id == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The person who dispatched the stock cannot be the one who receives it.'))
            counts = []
            for line in order.line_ids:
                qty = received.get(line.id, line.qty)
                if qty < 0 or qty > line.qty + 0.0001:
                    raise UserError(self.env._('%(product)s: counted %(qty)s of the %(sent)s sent.', product=line.product_id.display_name, qty=qty, sent=line.qty))
                counts.append((line, qty))
            short = [(l, l.qty - q) for l, q in counts if l.qty - q > 0.0001]
            if short and not reason:
                raise UserError(self.env._('Some stock did not arrive: give the reason for the loss.'))
            transit = order._transit_location()
            picking = order._move(transit, order.to_site_id.warehouse_id.lot_stock_id, counts, order.to_site_id, 'receipt')
            vals = {'state': 'received', 'received_by_id': user.id, 'received_at': fields.Datetime.now(), 'receipt_picking_id': picking.id}
            if short:
                loss = order._move(transit, order._loss_location(), short, order.to_site_id, 'transit loss')
                vals.update({'loss_picking_id': loss.id, 'loss_reason_id': reason.id})
            for line, qty in counts:
                line.sudo().write({'received_qty': qty})
            order.write(vals)
            self.env['mos.audit.event'].record('transfer_received', order, f'{order.name}: ' + (f'short {[(l.product_id.display_name, q) for l, q in short]}' if short else 'complete'))

    def action_cancel(self):
        user = self.env.user
        for order in self:
            if order.state == 'draft':
                order.write({'state': 'cancelled'})
            elif order.state == 'dispatched':
                if not (user.has_group('mos_inbound.group_mos_storekeeper') or user.has_group('mos_core.group_mos_admin')):
                    raise UserError(self.env._('Only a storekeeper can call stock back.'))
                order._move(order._transit_location(), order.from_site_id.warehouse_id.lot_stock_id, [(l, l.qty) for l in order.line_ids], order.from_site_id, 'recalled')
                order.write({'state': 'cancelled'})
            else:
                raise UserError(self.env._('A received or cancelled transfer cannot be cancelled.'))

    @api.model
    def _cron_flag_late(self):
        days = self.env['ir.config_parameter'].sudo().get_int('mos_stock_plus.transit_alert_days', 3)
        late = self.sudo().search([('state', '=', 'dispatched')]).filtered(lambda o: o.days_in_transit > days)
        for order in late:
            self.env['mos.exception'].sudo().raise_for('transit_late', order, self.env._('%(name)s has been in transit for %(n)s days', name=order.name, n=order.days_in_transit), severity='medium')
        return len(late)


class MosTransferOrderLine(models.Model):
    _name = 'mos.transfer.order.line'
    _description = 'MOS transfer line'

    order_id = fields.Many2one('mos.transfer.order', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('is_storable', '=', True)]")
    lot_id = fields.Many2one('stock.lot', domain="[('product_id', '=', product_id)]")
    qty = fields.Float(required=True, digits=(14, 1))
    received_qty = fields.Float(readonly=True, digits=(14, 1))
    uom_id = fields.Many2one(related='product_id.uom_id')

    @api.constrains('qty')
    def _check_qty(self):
        for line in self:
            if line.qty <= 0:
                raise ValidationError(self.env._('Send a quantity above zero.'))

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['mos.transfer.order'].browse([v['order_id'] for v in vals_list if v.get('order_id')])
        if any(o.state != 'draft' for o in orders):
            raise UserError(self.env._('Items can only be added to a draft transfer.'))
        return super().create(vals_list)

    def write(self, vals):
        if any(l.order_id.state != 'draft' for l in self) and set(vals) - {'received_qty'}:
            raise UserError(self.env._('The items of a dispatched transfer cannot change.'))
        return super().write(vals)

    def unlink(self):
        if any(l.order_id.state != 'draft' for l in self):
            raise UserError(self.env._('The items of a dispatched transfer cannot change.'))
        return super().unlink()
