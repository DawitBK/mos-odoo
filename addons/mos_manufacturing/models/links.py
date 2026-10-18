from odoo import api, models


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    def _mos_after_confirm(self):
        super()._mos_after_confirm()
        for visit in self.filtered(lambda v: v.flow != 'B' and v.purpose == 'inbound_po'):
            self.env['mos.batch'].for_visit(visit)


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    def decide(self, decision, deduction_pct=0.0, note=''):
        result = super().decide(decision, deduction_pct, note)
        for insp in self:
            visit = insp.visit_id
            if visit.flow == 'B' and insp.decision != 'reject':
                self.env['mos.batch'].for_visit(visit)
        return result


class MosDeliveryOrder(models.Model):
    """The kilograms that leave on a delivery are tied to the batches they came from, oldest released batch first, so a customer
    complaint leads back to the wheat (FR-MFG-009). Quantity that no batch can cover is recorded as untraced."""

    _inherit = 'mos.delivery.order'

    def action_dispatch(self):
        planned = {d.id: (d.product_id, d.visit_id.ticket_id.net_weight) for d in self if d.state == 'released'}
        result = super().action_dispatch()
        for delivery in self:
            product, qty = planned.get(delivery.id, (self.env['product.product'], 0.0))
            if product and qty:
                delivery._mos_allocate_batches(product, qty)
        return result

    def _mos_allocate_batches(self, product, qty):
        self.ensure_one()
        Delivery = self.env['mos.batch.delivery'].sudo()
        batches = self.env['mos.batch'].sudo().search(
            [('kind', 'in', ('flour', 'packed')), ('product_id', '=', product.id), ('state', '=', 'released'), ('remaining_qty', '>', 0)], order='id'
        )
        left = qty
        for batch in batches:
            if left <= 0.0001:
                break
            piece = min(batch.remaining_qty, left)
            Delivery.create({'batch_id': batch.id, 'delivery_id': self.id, 'qty': piece})
            left -= piece
        if left > 0.0001:
            Delivery.create({'delivery_id': self.id, 'qty': left})
