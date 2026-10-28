from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosGateVisitLine(models.Model):
    """One item of a supplier's delivery note: what the supplier says is on the truck, against what the purchase order asked for."""

    _name = 'mos.gate.visit.line'
    _description = 'MOS delivery item'
    _order = 'visit_id, sequence, id'

    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Item', required=True)
    uom_id = fields.Many2one('uom.uom', string='Unit', compute='_compute_uom', store=True, readonly=False, required=True)
    declared_qty = fields.Float(string='Declared quantity', digits=(14, 3), required=True)
    bags = fields.Integer()
    ordered_qty = fields.Float(string='Ordered', digits=(14, 3), compute='_compute_ordered')
    outstanding_qty = fields.Float(string='Still to receive', digits=(14, 3), compute='_compute_ordered')
    over_order = fields.Boolean(string='Above what is still due', compute='_compute_ordered')
    note = fields.Char()

    @api.depends('product_id')
    def _compute_uom(self):
        for line in self:
            line.uom_id = line.product_id.uom_id

    @api.depends('visit_id.purchase_id', 'product_id', 'uom_id', 'declared_qty')
    def _compute_ordered(self):
        for line in self:
            order_lines = line.visit_id.purchase_id.sudo().order_line.filtered(lambda l: l.product_id == line.product_id)
            ordered = sum(order_lines.mapped('product_uom_qty'))
            received = sum(order_lines.mapped('qty_received'))
            line.ordered_qty = ordered
            line.outstanding_qty = max(ordered - received, 0.0)
            same_unit = not line.uom_id or line.uom_id == line.product_id.uom_id
            line.over_order = bool(order_lines) and same_unit and line.declared_qty > line.outstanding_qty + 0.0005

    @api.constrains('declared_qty')
    def _check_positive(self):
        for line in self:
            if line.declared_qty <= 0:
                raise ValidationError(self.env._('The declared quantity of %s must be above zero.', line.product_id.display_name))

    @api.constrains('product_id', 'visit_id')
    def _check_on_order(self):
        """The items come from the purchase order: an item that is not on it is not accepted here, the order is changed first."""
        for line in self:
            order = line.visit_id.purchase_id
            if order and line.product_id not in order.sudo().order_line.product_id:
                raise ValidationError(
                    self.env._('%(item)s is not on purchase order %(order)s. Change the order first, or leave the item off this delivery.', item=line.product_id.display_name, order=order.name)
                )


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    delivery_date = fields.Date(default=fields.Date.context_today)
    delivery_line_ids = fields.One2many('mos.gate.visit.line', 'visit_id', string='Delivery items', copy=False)
    declared_totals = fields.Html(compute='_compute_declared_totals', sanitize=False)
    entry_state = fields.Selection(
        [('entered', 'Entered'), ('approved', 'Approved for inspection'), ('rejected', 'Rejected at the gate')],
        string='Delivery entry',
        default='entered',
        readonly=True,
        copy=False,
        tracking=True,
    )
    entry_reject_reason = fields.Char(readonly=True, copy=False)

    @api.depends('delivery_line_ids.declared_qty', 'delivery_line_ids.uom_id', 'delivery_line_ids.bags')
    def _compute_declared_totals(self):
        """One total for each unit of measure, never one number for tonnes and kilograms together (FR-RPT-005)."""
        for visit in self:
            totals, bags = {}, 0
            for line in visit.delivery_line_ids:
                totals[line.uom_id.name] = totals.get(line.uom_id.name, 0.0) + line.declared_qty
                bags += line.bags
            parts = [Markup('<strong>{:,.3f}</strong> {}').format(qty, escape(unit)) for unit, qty in sorted(totals.items())]
            if bags:
                parts.append(Markup('<strong>{:,}</strong> bags').format(bags))
            visit.declared_totals = Markup(' &nbsp;+&nbsp; ').join(parts) if parts else False

    # -- the client's Approve and Reject on the entry --------------------------------------------------------------------------
    def action_entry_approve(self):
        """The delivery matches an open purchase order: it goes on to inspection (client question C-41, default reading)."""
        for visit in self:
            if visit.purpose != 'inbound_po':
                raise UserError(self.env._('Only a supplier delivery has an entry to approve.'))
            if visit.entry_state != 'entered' or visit.state in ('cancelled', 'exited'):
                raise UserError(self.env._('This delivery entry can no longer be approved.'))
            order = visit.purchase_id
            if not order or order.state not in ('purchase', 'done'):
                raise UserError(self.env._('Choose a confirmed purchase order for this delivery.'))
            if visit.partner_id and visit.partner_id != order.partner_id:
                raise UserError(self.env._('The supplier on the delivery (%(a)s) is not the supplier of the order (%(b)s).', a=visit.partner_id.name, b=order.partner_id.name))
            if not visit.delivery_line_ids:
                raise UserError(self.env._('Enter the items on the delivery note first.'))
            visit.write({'entry_state': 'approved', 'partner_id': order.partner_id.id})
            visit.message_post(body=self.env._('Delivery entry approved for inspection by %s.', self.env.user.name))
            self.env['mos.audit.event'].record('delivery_entry_approved', visit, f'{visit.name}: {order.name}, {visit.delivery_note or "no delivery note number"}')

    def action_entry_reject(self, reason):
        for visit in self:
            if not (reason or '').strip():
                raise UserError(self.env._('A reason is required to reject a delivery.'))
            if visit.entry_state != 'entered':
                raise UserError(self.env._('This delivery entry can no longer be rejected.'))
            visit.action_cancel()
            visit.write({'entry_state': 'rejected', 'entry_reject_reason': reason})
            visit.message_post(body=self.env._('Delivery rejected at the gate by %(who)s: %(why)s', who=self.env.user.name, why=reason))
            self.env['mos.audit.event'].record('delivery_entry_rejected', visit, f'{visit.name}: {reason}')

    def action_entry_reject_dialog(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Reject the delivery'),
            'res_model': 'mos.delivery.reject',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_visit_id': self.id},
        }

    def action_start_weighing(self):
        if self.env['ir.config_parameter'].sudo().get_bool('mos_deliveries.require_entry_approval'):
            for visit in self:
                if visit.purpose == 'inbound_po' and visit.entry_state != 'approved':
                    raise UserError(self.env._('Approve the delivery entry before the truck is weighed.'))
        return super().action_start_weighing()


class MosDeliveryReject(models.TransientModel):
    _name = 'mos.delivery.reject'
    _description = 'Reject a delivery at the gate'

    visit_id = fields.Many2one('mos.gate.visit', required=True, readonly=True)
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.visit_id.action_entry_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
