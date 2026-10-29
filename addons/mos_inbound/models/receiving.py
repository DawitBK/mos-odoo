from markupsafe import Markup, escape

from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    mos_short_qty = fields.Float(string='Rejected / short', compute='_compute_mos_short', digits='Product Unit', help='What was ordered and did not come in as accepted goods.')

    @api.depends('product_uom_qty', 'quantity', 'picking_id.state')
    def _compute_mos_short(self):
        for move in self:
            move.mos_short_qty = max(move.product_uom_qty - move.quantity, 0.0) if move.picking_id.state in ('assigned', 'done') else 0.0


class StockPicking(models.Model):
    """The goods receiving screen of the client (page 9): where the goods came in, what was ordered and what was accepted, and
    whether the receipt can be turned into a goods receipt note (GRN)."""

    _inherit = 'stock.picking'

    mos_warehouse_id = fields.Many2one('stock.warehouse', related='picking_type_id.warehouse_id', string='Warehouse')
    mos_item_count = fields.Integer(compute='_compute_mos_totals', string='Total items')
    mos_ordered_totals = fields.Html(compute='_compute_mos_totals', sanitize=False, string='Total ordered quantity')
    mos_accepted_totals = fields.Html(compute='_compute_mos_totals', sanitize=False, string='Total accepted quantity')
    mos_short_totals = fields.Html(compute='_compute_mos_totals', sanitize=False, string='Total rejected / short quantity')
    mos_receiving_status = fields.Selection(
        [('waiting', 'Waiting for the goods'), ('ready', 'Ready for GRN'), ('short', 'Ready for GRN, with a short quantity'), ('done', 'GRN issued'), ('cancel', 'Rejected')],
        compute='_compute_mos_status',
        string='Receiving status',
    )

    @api.model
    def _mos_totals_html(self, totals):
        parts = [Markup('<strong>{:,.1f}</strong> {}').format(qty, escape(unit)) for unit, qty in sorted(totals.items()) if qty]
        return Markup(' &nbsp;+&nbsp; ').join(parts) if parts else Markup('<strong>0</strong>')

    @api.depends('move_ids.product_uom_qty', 'move_ids.quantity', 'move_ids.mos_short_qty', 'move_ids.uom_id')
    def _compute_mos_totals(self):
        for picking in self:
            ordered, accepted, short = {}, {}, {}
            for move in picking.move_ids:
                unit = move.uom_id.name
                ordered[unit] = ordered.get(unit, 0.0) + move.product_uom_qty
                accepted[unit] = accepted.get(unit, 0.0) + move.quantity
                short[unit] = short.get(unit, 0.0) + move.mos_short_qty
            picking.mos_item_count = len(picking.move_ids)
            picking.mos_ordered_totals = self._mos_totals_html(ordered)
            picking.mos_accepted_totals = self._mos_totals_html(accepted)
            picking.mos_short_totals = self._mos_totals_html(short)

    @api.depends('state', 'move_ids.quantity', 'move_ids.picked', 'move_ids.mos_short_qty')
    def _compute_mos_status(self):
        for picking in self:
            if picking.state == 'done':
                picking.mos_receiving_status = 'done'
            elif picking.state == 'cancel':
                picking.mos_receiving_status = 'cancel'
            elif picking.move_ids and all(move.picked and move.quantity > 0 for move in picking.move_ids):
                picking.mos_receiving_status = 'short' if any(move.mos_short_qty > 0.0001 for move in picking.move_ids) else 'ready'
            else:
                picking.mos_receiving_status = 'waiting'

    def button_validate(self):
        """The person who generates the GRN is the receiving officer of a receipt that had none."""
        for picking in self.filtered(lambda p: p.picking_type_code == 'incoming' and not p.user_id):
            picking.user_id = self.env.user
        return super().button_validate()
