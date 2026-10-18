from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    """An order is closed when it is received (within the short-delivery tolerance) and invoiced, or closed short by a manager with a
    reason. A closed order takes no more deliveries (FR-PRC-012)."""

    _inherit = 'purchase.order'

    mos_close_state = fields.Selection([('open', 'Open'), ('closed', 'Closed'), ('closed_short', 'Closed short')], default='open', string='Closure', copy=False, tracking=True)
    mos_closed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    mos_close_reason = fields.Char(readonly=True, copy=False)

    def action_close_short(self, reason):
        """Stop expecting the rest of the order: the open receipt is cancelled and the order takes no more deliveries."""
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can close an order short.'))
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        for order in self:
            if order.state != 'purchase' or order.mos_close_state != 'open':
                raise UserError(self.env._('Only an open, confirmed order can be closed short.'))
            if not any(l.qty_received for l in order.order_line):
                raise UserError(self.env._('Nothing has been received: cancel the order instead of closing it short.'))
            pending = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel') and p.picking_type_code == 'incoming')
            pending.action_cancel()
            order.sudo().write({'mos_close_state': 'closed_short', 'mos_closed_by_id': user.id, 'mos_close_reason': reason})
            order.message_post(body=self.env._('Closed short: %s', reason))
            self.env['mos.audit.event'].record('po_closed_short', order, f'{order.name}: {reason}')

    def _mos_close_if_complete(self):
        """Received within the tolerance and invoiced for what was received: the order closes by itself."""
        closed = self.browse()
        for order in self.filtered(lambda o: o.state == 'purchase' and o.mos_close_state == 'open'):
            lines = order.order_line.filtered(lambda l: l.product_id.is_storable)
            if not lines:
                continue
            done = True
            for line in lines:
                policy = self.env['mos.receipt.policy'].sudo().for_product(line.product_id)
                tolerance = (policy.under_pct if policy else 0.0) / 100.0
                if line.qty_received < line.product_qty * (1 - tolerance) - 0.0001:
                    done = False
                    break
                invoices = self.env['mos.supplier.invoice'].sudo().search([('purchase_id', '=', order.id), ('product_id', '=', line.product_id.id), ('state', 'not in', ('draft', 'cancelled'))])
                if sum(invoices.mapped('qty')) < line.qty_received - 0.0001:
                    done = False
                    break
            if done:
                order.sudo().write({'mos_close_state': 'closed', 'mos_close_reason': self.env._('Received and invoiced')})
                order.message_post(body=self.env._('Closed: received within the tolerance and invoiced.'))
                closed |= order
        return closed

    @api.model
    def _cron_close_orders(self):
        return len(self.search([('state', '=', 'purchase'), ('mos_close_state', '=', 'open')])._mos_close_if_complete())


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    def _mos_preflight(self, product=None, qty=0.0):
        problems = super()._mos_preflight(product, qty)
        order = self.sudo().purchase_id
        if order and order.mos_close_state != 'open':
            problems.append(self.env._('the purchase order %s is closed', order.name))
        return problems


class MosSupplierInvoice(models.Model):
    """An invoice that completes an order closes it."""

    _inherit = 'mos.supplier.invoice'

    def action_match(self):
        result = super().action_match()
        self.mapped('purchase_id')._mos_close_if_complete()
        return result
