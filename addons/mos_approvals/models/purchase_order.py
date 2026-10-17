from odoo import models


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['purchase.order', 'mos.approvable']

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.currency_id._convert(self.amount_total, self.company_id.currency_id, self.company_id, self.date_order or self.create_date) if self.currency_id else self.amount_total

    def _mos_approval_site(self):
        self.ensure_one()
        return self.env['mos.site'].sudo().search([('warehouse_id', '=', self.picking_type_id.warehouse_id.id)], limit=1)

    def button_confirm(self):
        # A purchase order inside an approval band cannot be confirmed until its chain has approved it.
        for order in self.filtered(lambda o: o.state in ('draft', 'sent')):
            order._mos_require_approved()
        return super().button_confirm()

    def write(self, vals):
        # Once submitted, the content is locked until the chain decides (a returned document opens again).
        if self.env.context.get('mos_approval_internal'):
            return super().write(vals)
        guarded = {'order_line', 'partner_id', 'amount_total', 'date_order'}
        if guarded & set(vals):
            for order in self:
                if order.mos_approval_state == 'pending':
                    from odoo.exceptions import UserError

                    raise UserError(self.env._('%s is waiting for approval and cannot be edited; recall it first.', order.display_name))
        return super().write(vals)
