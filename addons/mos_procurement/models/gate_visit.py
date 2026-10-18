from odoo import models
from odoo.exceptions import UserError


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    def _mos_check_receipt(self, order, product, qty):
        """Refuse or flag an excess over the quantity ordered, by the receipt policy (FR-PRC-016)."""
        super()._mos_check_receipt(order, product, qty)
        lines = order.order_line.filtered(lambda line: line.product_id == product)
        ordered = sum(lines.mapped('product_qty'))
        policy = self.env['mos.receipt.policy'].sudo().for_product(product)
        if not ordered or not policy:
            return
        received = sum(lines.mapped('qty_received'))
        excess_pct = (received + qty - ordered) / ordered * 100.0
        if excess_pct <= policy.over_pct + 0.0001:
            return
        text = self.env._(
            'This load takes %(product)s to %(total)s kg against %(ordered)s kg ordered: %(pct)s%% over, the policy allows %(allowed)s%%.',
            product=product.display_name,
            total=round(received + qty),
            ordered=round(ordered),
            pct=round(excess_pct, 2),
            allowed=policy.over_pct,
        )
        if policy.over_action == 'block':
            raise UserError(text)
        self.sudo().message_post(body=text)
        self.env['mos.gate.incident'].sudo().create({'site_id': self.site_id.id, 'visit_id': self.id, 'kind': 'other', 'severity': 'medium', 'description': text})
