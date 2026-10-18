from odoo import api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    """A customer's order. Credit exposure is checked before it can be confirmed: open invoices plus this order must
    stay inside the customer's limit, or the order goes through the approval matrix first (FR-SAL-004, 016)."""

    _name = 'sale.order'
    _inherit = ['sale.order', 'mos.approvable']

    mos_exposure = fields.Float(compute='_compute_mos_exposure', string='Exposure with this order')
    mos_credit_exceeded = fields.Boolean(compute='_compute_mos_exposure', string='Over the credit limit')
    mos_delivery_ids = fields.One2many('mos.delivery.order', 'sale_id', string='Delivery orders')

    @api.depends('amount_total', 'partner_id', 'partner_id.mos_credit_limit', 'partner_id.mos_credit_mode')
    def _compute_mos_exposure(self):
        for order in self:
            partner = order.partner_id.commercial_partner_id
            owed = partner.mos_owed
            order.mos_exposure = owed + order.amount_total
            limit = partner.mos_credit_limit
            order.mos_credit_exceeded = bool(partner.mos_credit_mode != 'none' and order.mos_exposure > limit + 0.005)

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.amount_total

    def _mos_approval_site(self):
        self.ensure_one()
        return self.env['mos.site'].sudo().search([('warehouse_id', '=', self.warehouse_id.id)], limit=1)

    def _mos_approval_required(self):
        self.ensure_one()
        if self.mos_credit_exceeded and self.partner_id.commercial_partner_id.mos_credit_mode == 'block':
            return True
        return bool(super()._mos_approval_required())

    def _mos_submit_possible(self):
        # An order over its credit limit but with no matching band still needs somebody: surface it clearly.
        self.ensure_one()
        return bool(self.env['mos.approval.rule'].sudo().matching('sale.order', self._mos_approval_amount(), self._mos_approval_site()))

    def action_mos_submit_approval(self):
        for order in self:
            if not order._mos_submit_possible():
                raise UserError(self.env._('The approval matrix has no rule for this order, so nobody can approve its credit exposure. Ask an administrator to add one.'))
        return super().action_mos_submit_approval()

    def action_confirm(self):
        for order in self.filtered(lambda o: o.state in ('draft', 'sent')):
            order._mos_require_approved()
        return super().action_confirm()

    def action_mos_create_delivery(self):
        self.ensure_one()
        if self.state != 'sale':
            raise UserError(self.env._('Confirm the order before preparing a delivery.'))
        delivery = self.env['mos.delivery.order'].create({'sale_id': self.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.delivery.order', 'res_id': delivery.id, 'view_mode': 'form'}
