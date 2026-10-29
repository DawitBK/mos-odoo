from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError

STATUSES = [('available', 'Available'), ('quarantine', 'Quarantine'), ('blocked', 'Blocked')]


class StockLocation(models.Model):
    _inherit = 'stock.location'

    mos_stock_status = fields.Selection(STATUSES, string='MOS stock status', default='available', required=True, help='Stock held in a quarantine or blocked location is not available to use.')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    mos_reorder_min = fields.Float(string='Reorder level', help='Order more when what is available and on order falls below this.')
    mos_reorder_qty = fields.Float(string='Reorder quantity', help='The quantity to order, at least.')
    mos_safety_stock = fields.Float(string='Safety stock', help='What must always be left. The reorder report marks an item that is below it.')
    mos_max_level = fields.Float(string='Maximum level', help='The most to hold, counting what is on order. A suggested order never goes beyond it. Zero means no maximum.')

    @api.constrains('mos_reorder_min', 'mos_reorder_qty', 'mos_safety_stock', 'mos_max_level')
    def _check_reorder(self):
        for product in self:
            if min(product.mos_reorder_min, product.mos_reorder_qty, product.mos_safety_stock, product.mos_max_level) < 0:
                raise ValidationError(self.env._('Reorder level, reorder quantity, safety stock and maximum level cannot be negative.'))
            if product.mos_reorder_min and product.mos_safety_stock > product.mos_reorder_min:
                raise ValidationError(self.env._('The safety stock cannot be above the reorder level.'))
            if product.mos_max_level and max(product.mos_reorder_min, product.mos_safety_stock) > product.mos_max_level:
                raise ValidationError(self.env._('The maximum level cannot be below the reorder level or the safety stock.'))


class MosReorderReport(models.Model):
    """What to buy now: available stock plus open purchase quantity against the reorder level (FR-INV-020)."""

    _name = 'mos.reorder.report'
    _description = 'MOS reorder report'
    _auto = False
    _order = 'shortfall desc'

    product_id = fields.Many2one('product.product', readonly=True)
    reorder_min = fields.Float(readonly=True)
    reorder_qty = fields.Float(readonly=True)
    safety_stock = fields.Float(readonly=True)
    max_level = fields.Float(readonly=True)
    below_safety = fields.Boolean(readonly=True, string='Below safety stock')
    on_hand = fields.Float(string='Available now', readonly=True)
    on_order = fields.Float(string='Still to arrive', readonly=True)
    shortfall = fields.Float(readonly=True)
    suggested_qty = fields.Float(string='Order this much', readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH hand AS (
                    SELECT sq.product_id, SUM(sq.quantity) AS qty
                      FROM stock_quant sq
                      JOIN stock_location sl ON sl.id = sq.location_id
                     WHERE sl.usage = 'internal' AND sl.mos_stock_status = 'available'
                  GROUP BY sq.product_id
                ), ordered AS (
                    SELECT pol.product_id, SUM(GREATEST(pol.product_qty - COALESCE(pol.qty_received, 0), 0)) AS qty
                      FROM purchase_order_line pol
                      JOIN purchase_order po ON po.id = pol.order_id
                     WHERE po.state = 'purchase'
                  GROUP BY pol.product_id
                )
                SELECT pp.id AS id,
                       pp.id AS product_id,
                       pt.mos_reorder_min AS reorder_min,
                       pt.mos_reorder_qty AS reorder_qty,
                       pt.mos_safety_stock AS safety_stock,
                       pt.mos_max_level AS max_level,
                       COALESCE(h.qty, 0) < pt.mos_safety_stock AS below_safety,
                       COALESCE(h.qty, 0) AS on_hand,
                       COALESCE(o.qty, 0) AS on_order,
                       GREATEST(pt.mos_reorder_min - COALESCE(h.qty, 0) - COALESCE(o.qty, 0), 0) AS shortfall,
                       CASE WHEN pt.mos_reorder_min - COALESCE(h.qty, 0) - COALESCE(o.qty, 0) > 0
                            THEN CASE WHEN pt.mos_max_level > 0
                                      THEN LEAST(GREATEST(pt.mos_reorder_qty, pt.mos_reorder_min - COALESCE(h.qty, 0) - COALESCE(o.qty, 0)),
                                                 GREATEST(pt.mos_max_level - COALESCE(h.qty, 0) - COALESCE(o.qty, 0), 0))
                                      ELSE GREATEST(pt.mos_reorder_qty, pt.mos_reorder_min - COALESCE(h.qty, 0) - COALESCE(o.qty, 0)) END
                            ELSE 0 END AS suggested_qty
                  FROM product_product pp
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
             LEFT JOIN hand h ON h.product_id = pp.id
             LEFT JOIN ordered o ON o.product_id = pp.id
                 WHERE (pt.mos_reorder_min > 0 OR pt.mos_safety_stock > 0) AND pp.active
            )
            """
        )


class MosStatusChange(models.Model):
    """Move stock between available, quarantine and blocked, with a reason, and release it only on a second person's say-so (FR-INV-010..012)."""

    _name = 'mos.stock.status.change'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS stock status change'
    _order = 'id desc'
    _mos_final_states = ('done', 'rejected', 'cancelled')

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(required=True, digits=(14, 1))
    from_status = fields.Selection(STATUSES, required=True, default='available')
    to_status = fields.Selection(STATUSES, required=True, default='quarantine')
    location_id = fields.Many2one('stock.location', string='Silo / shelf', domain="[('usage', '=', 'internal')]", help='Where the stock is, or goes back to, when it is available. Empty is the warehouse stock location.')
    reason_id = fields.Many2one('mos.reason', required=True, domain=[('domain', '=', 'status_change')])
    note = fields.Char()
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    state = fields.Selection(
        [('draft', 'Requested'), ('approved', 'Approved'), ('done', 'Done'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.constrains('qty', 'from_status', 'to_status')
    def _check(self):
        for change in self:
            if change.qty <= 0:
                raise ValidationError(self.env._('Move a quantity above zero.'))
            if change.from_status == change.to_status:
                raise ValidationError(self.env._('The stock is already in that status.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.stock.status.change')
        return super().create(vals_list)

    @api.model
    def status_location(self, site, status):
        warehouse = site.warehouse_id
        if status == 'available':
            return warehouse.lot_stock_id
        label = 'MOS Quarantine' if status == 'quarantine' else 'MOS Blocked'
        location = self.env['stock.location'].sudo().search([('name', '=', label), ('location_id', '=', warehouse.view_location_id.id)], limit=1)
        return location or self.env['stock.location'].sudo().create(
            {'name': label, 'location_id': warehouse.view_location_id.id, 'usage': 'internal', 'mos_stock_status': status}
        )

    def action_approve(self):
        user = self.env.user
        if not (user.has_group('mos_inventory.group_mos_inventory_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only an inventory manager can approve a status change.'))
        for change in self:
            if change.state != 'draft':
                raise UserError(self.env._('Only a requested change can be approved.'))
            if change.requested_by_id == user:
                raise UserError(self.env._('You cannot approve your own status change.'))
            change.write({'state': 'approved', 'approved_by_id': user.id})

    def action_apply(self):
        for change in self:
            if change.state != 'approved':
                raise UserError(self.env._('Approve the change before moving the stock.'))
            src = change.location_id if (change.from_status == 'available' and change.location_id) else self.status_location(change.site_id, change.from_status)
            dst = change.location_id if (change.to_status == 'available' and change.location_id) else self.status_location(change.site_id, change.to_status)
            quants = self.env['stock.quant'].sudo().search([('product_id', '=', change.product_id.id), ('location_id', '=', src.id)])
            have = sum(quants.mapped('quantity'))
            if have + 0.0001 < change.qty:
                raise UserError(self.env._('Only %(have)s kg of %(product)s is in %(status)s.', have=have, product=change.product_id.display_name, status=change.from_status))
            picking_type = change.site_id.warehouse_id.int_type_id
            picking = self.env['stock.picking'].sudo().create(
                {
                    'picking_type_id': picking_type.id,
                    'location_id': src.id,
                    'location_dest_id': dst.id,
                    'origin': change.name,
                    'move_ids': [
                        (0, 0, {'product_id': change.product_id.id, 'product_uom_qty': change.qty, 'location_id': src.id, 'location_dest_id': dst.id}),
                    ],
                }
            )
            picking.action_confirm()
            picking.move_ids.write({'quantity': change.qty, 'picked': True})
            result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._('The internal transfer needs attention in Inventory.'))
            change.write({'state': 'done', 'picking_id': picking.id})
            self.env['mos.audit.event'].record('stock_status_changed', change, f'{change.name}: {change.qty} {change.product_id.display_name} {change.from_status} -> {change.to_status}')

    def action_reject(self):
        for change in self:
            if change.state not in ('draft', 'approved'):
                raise UserError(self.env._('This change is closed.'))
            change.state = 'rejected'
