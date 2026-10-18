from odoo import fields, models
from odoo.exceptions import UserError


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    excess_approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    def action_approve_excess(self, reason):
        """A procurement manager lets a load go over the order tolerance (FRD 9.5 #13)."""
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can approve an excess.'))
        if not reason:
            raise UserError(self.env._('Give the reason for accepting the excess.'))
        for visit in self:
            visit.sudo().write({'excess_approved_by_id': user.id})
            self.env['mos.audit.event'].record('excess_approved', visit, f'{visit.name}: {reason}')

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
        if policy.over_action == 'approve':
            if not self.excess_approved_by_id:
                raise UserError(self.env._('%s A procurement manager must approve the excess first.', text))
            return
        self.sudo().message_post(body=text)
        self.env['mos.gate.incident'].sudo().create({'site_id': self.site_id.id, 'visit_id': self.id, 'kind': 'other', 'severity': 'medium', 'description': text})
