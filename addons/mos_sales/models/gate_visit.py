from odoo import fields, models


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    delivery_order_id = fields.Many2one('mos.delivery.order', string='Delivery order', readonly=True, copy=False)

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        if self.purpose == 'outbound_do' and (not self.delivery_order_id or self.delivery_order_id.state != 'dispatched'):
            problems.append(self.env._('the delivery has not been dispatched'))
        return problems
