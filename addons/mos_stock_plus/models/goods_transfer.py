from markupsafe import Markup, escape

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosGoodsTransfer(models.Model):
    """The client's Goods Transfer screen (page 12): stock moved from one location to another inside a site, posted as one stock move
    (FR-INV-007, intra-site). Moving stock between sites is the transfer order, which passes through transit."""

    _name = 'mos.goods.transfer'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS goods transfer'
    _order = 'id desc'
    _mos_final_states = ('posted', 'cancelled')
    _mos_editable_after_post = {'state', 'posted_by_id', 'posted_at', 'picking_id'}

    name = fields.Char(string='Transfer No.', readonly=True, copy=False, default='New')
    state = fields.Selection([('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True, copy=False)
    transfer_date = fields.Date(required=True, default=fields.Date.context_today)
    reference_type = fields.Selection(
        [('internal', 'Internal transfer'), ('production', 'To production'), ('packing', 'To packing'), ('other', 'Other')], default='internal', required=True
    )
    reference_no = fields.Char(string='Reference No.')
    remarks = fields.Char(size=500)  # 500 characters on the screen, in the column and here
    from_warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True, default=lambda self: self._default_warehouse())
    from_location_id = fields.Many2one('stock.location', string='Storage location / bin', required=True, domain="[('usage', '=', 'internal'), ('warehouse_id', '=', from_warehouse_id)]")
    to_warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True, default=lambda self: self._default_warehouse())
    to_location_id = fields.Many2one('stock.location', string='Storage location / bin', required=True, domain="[('usage', '=', 'internal'), ('warehouse_id', '=', to_warehouse_id)]")
    site_id = fields.Many2one('mos.site', compute='_compute_site', store=True, readonly=True, string='Site')
    line_ids = fields.One2many('mos.goods.transfer.line', 'transfer_id', string='Items to transfer', copy=True)
    item_count = fields.Integer(compute='_compute_totals', string='Total items')
    total_quantity = fields.Html(compute='_compute_totals', sanitize=False, string='Total transfer quantity')
    posted_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    posted_at = fields.Datetime(readonly=True, copy=False)
    picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)

    @api.model
    def _default_warehouse(self):
        site = self.env.user._mos_current_site()
        return site.warehouse_id or self.env['stock.warehouse'].search([], limit=1)

    @api.depends('from_warehouse_id')
    def _compute_site(self):
        for transfer in self:
            transfer.site_id = self.env['mos.site'].sudo().search([('warehouse_id', '=', transfer.from_warehouse_id.id)], limit=1)

    @api.depends('line_ids.qty', 'line_ids.uom_id')
    def _compute_totals(self):
        """One total for each unit of measure, never one number for kilograms and pieces together."""
        for transfer in self:
            totals = {}
            for line in transfer.line_ids:
                totals[line.uom_id.name] = totals.get(line.uom_id.name, 0.0) + line.qty
            transfer.item_count = len(transfer.line_ids)
            parts = [Markup('<strong>{:,.1f}</strong> {}').format(qty, escape(unit)) for unit, qty in sorted(totals.items())]
            transfer.total_quantity = Markup(' &nbsp;+&nbsp; ').join(parts) if parts else False

    @api.onchange('from_warehouse_id')
    def _onchange_from_warehouse(self):
        if self.from_location_id.warehouse_id != self.from_warehouse_id:
            self.from_location_id = self.from_warehouse_id.lot_stock_id

    @api.onchange('to_warehouse_id')
    def _onchange_to_warehouse(self):
        if self.to_location_id.warehouse_id != self.to_warehouse_id:
            self.to_location_id = self.to_warehouse_id.lot_stock_id

    @api.constrains('from_location_id', 'to_location_id', 'from_warehouse_id', 'to_warehouse_id', 'remarks')
    def _check_places(self):
        for transfer in self:
            if transfer.from_location_id == transfer.to_location_id:
                raise ValidationError(self.env._('The source and the destination are the same place: choose where the goods go.'))
            if transfer.from_location_id.warehouse_id != transfer.from_warehouse_id or transfer.to_location_id.warehouse_id != transfer.to_warehouse_id:
                raise ValidationError(self.env._('A storage location must belong to the warehouse chosen with it.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.goods.transfer')
        return super().create(vals_list)

    # -- actions ---------------------------------------------------------------------------------------------------------
    def action_post(self):
        user = self.env.user
        for transfer in self:
            if transfer.state != 'draft':
                raise UserError(self.env._('Only a draft transfer can be posted.'))
            if not transfer.line_ids:
                raise UserError(self.env._('Add the items to transfer.'))
            if not (user.has_group('mos_inbound.group_mos_storekeeper') or user.has_group('mos_core.group_mos_admin')):
                raise UserError(self.env._('Only a storekeeper can post a stock transfer.'))
            if transfer.site_id not in user.mos_site_ids and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('You do not work at %s, so you cannot move its stock.', transfer.site_id.display_name))
            for line in transfer.line_ids:
                if line.qty > line.available_qty + 0.0001:
                    raise UserError(
                        self.env._('%(item)s: %(qty)s requested but only %(have)s is available in %(place)s.', item=line.product_id.display_name, qty=line.qty, have=line.available_qty, place=transfer.from_location_id.display_name)
                    )
            picking = transfer._post_picking()
            transfer.write({'state': 'posted', 'posted_by_id': user.id, 'posted_at': fields.Datetime.now(), 'picking_id': picking.id})
            self.env['mos.audit.event'].record('goods_transfer_posted', transfer, f'{transfer.name}: {transfer.from_location_id.display_name} -> {transfer.to_location_id.display_name}, {len(transfer.line_ids)} lines')

    def _post_picking(self):
        self.ensure_one()
        lines = self.line_ids
        picking = self.env['stock.picking'].sudo().create(
            {
                'picking_type_id': self.from_warehouse_id.int_type_id.id,
                'location_id': self.from_location_id.id,
                'location_dest_id': self.to_location_id.id,
                'origin': self.name,
                'move_ids': [
                    Command.create({'product_id': line.product_id.id, 'product_uom_qty': line.qty, 'location_id': self.from_location_id.id, 'location_dest_id': self.to_location_id.id}) for line in lines
                ],
            }
        )
        picking.action_confirm()
        for move, line in zip(picking.move_ids, lines):
            move.write({'quantity': line.qty, 'picked': True})
            if line.lot_id and move.move_line_ids:
                move.move_line_ids.write({'lot_id': line.lot_id.id})
        result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        if isinstance(result, dict):
            raise UserError(self.env._('The stock move for %s needs attention in Inventory.', self.name))
        return picking

    def action_cancel(self):
        for transfer in self:
            if transfer.state != 'draft':
                raise UserError(self.env._('A posted transfer cannot be cancelled: post a transfer back to undo it.'))
            transfer.write({'state': 'cancelled'})


class MosGoodsTransferLine(models.Model):
    _name = 'mos.goods.transfer.line'
    _description = 'MOS goods transfer line'
    _order = 'sequence, id'

    transfer_id = fields.Many2one('mos.goods.transfer', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Item', required=True, domain="[('is_storable', '=', True)]")
    lot_id = fields.Many2one('stock.lot', string='Batch No.', domain="[('product_id', '=', product_id)]")
    uom_id = fields.Many2one(related='product_id.uom_id', string='UOM')
    available_qty = fields.Float(string='Available qty (from)', compute='_compute_available', digits=(14, 1))
    qty = fields.Float(string='Transfer qty', required=True, digits=(14, 1))
    remarks = fields.Char()

    @api.depends('product_id', 'lot_id', 'transfer_id.from_location_id')
    def _compute_available(self):
        Quant = self.env['stock.quant'].sudo()
        for line in self:
            location = line.transfer_id.from_location_id
            if not (line.product_id and location):
                line.available_qty = 0.0
                continue
            domain = [('product_id', '=', line.product_id.id), ('location_id', '=', location.id), ('location_id.mos_stock_status', '=', 'available')]
            if line.lot_id:
                domain.append(('lot_id', '=', line.lot_id.id))
            line.available_qty = sum(Quant.search(domain).mapped('quantity'))

    @api.constrains('qty')
    def _check_qty(self):
        for line in self:
            if line.qty <= 0:
                raise ValidationError(self.env._('Transfer a quantity above zero.'))

    @api.model_create_multi
    def create(self, vals_list):
        transfers = self.env['mos.goods.transfer'].browse([v['transfer_id'] for v in vals_list if v.get('transfer_id')])
        if any(t.state != 'draft' for t in transfers):
            raise UserError(self.env._('Items can only be added to a draft transfer.'))
        return super().create(vals_list)

    def write(self, vals):
        if any(line.transfer_id.state != 'draft' for line in self):
            raise UserError(self.env._('The items of a posted transfer cannot change.'))
        return super().write(vals)

    def unlink(self):
        if any(line.transfer_id.state != 'draft' for line in self):
            raise UserError(self.env._('The items of a posted transfer cannot change.'))
        return super().unlink()
