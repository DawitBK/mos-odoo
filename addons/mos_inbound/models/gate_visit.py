from odoo import fields, models
from odoo.exceptions import UserError


class MosGateVisit(models.Model):
    """Flow A (bulk material inspected before unloading, FR-INB-001/002): the weighbridge and the lab
    feed the goods receipt without re-keying. The system prepares the receipt at the payable
    quantity; a storekeeper confirms it (ADR-0016)."""

    _inherit = 'mos.gate.visit'

    inspection_ids = fields.One2many('mos.lab.inspection', 'visit_id', string='Inspections')
    inspection_id = fields.Many2one('mos.lab.inspection', compute='_compute_inspection', string='Inspection')
    grn_picking_id = fields.Many2one('stock.picking', string='Goods receipt', readonly=True, copy=False)
    grn_state = fields.Selection(
        [('none', 'Not prepared'), ('prepared', 'Prepared'), ('confirmed', 'Confirmed'), ('rejected', 'Rejected load')],
        default='none',
        readonly=True,
        copy=False,
    )

    def _compute_inspection(self):
        for visit in self:
            visit.inspection_id = visit.inspection_ids[-1:] if visit.inspection_ids else False

    def _po_product(self):
        self.ensure_one()
        lines = self.sudo().purchase_id.order_line.filtered(lambda line: line.product_id.type == 'consu')
        if not lines:
            raise UserError(self.env._('The purchase order has no stock line to inspect.'))
        return lines[0].product_id

    def action_open_inspection(self):
        self.ensure_one()
        if self.inspection_ids.filtered(lambda i: i.state != 'decided'):
            raise UserError(self.env._('An inspection is already open for this visit.'))
        if self.state not in ('weighing', 'weighed'):
            raise UserError(self.env._('Open the inspection once the vehicle is on the weighbridge.'))
        return self.env['mos.lab.inspection'].create({'visit_id': self.id, 'product_id': self._po_product().id})

    def action_prepare_grn(self):
        """Prepare the goods receipt at the payable quantity. Safe to repeat: the same receipt is returned (FR-INB-007)."""
        self.ensure_one()
        if self.grn_picking_id:
            return self.grn_picking_id
        insp = self.inspection_id
        if not insp or insp.state != 'decided':
            raise UserError(self.env._('The inspection must be decided before the receipt is prepared.'))
        if insp.decision == 'reject':
            self.write({'grn_state': 'rejected'})
            self.env['mos.audit.event'].record('load_rejected', self, self.name)
            return False
        if not self.ticket_id or self.ticket_id.state != 'posted':
            raise UserError(self.env._('The weigh ticket must be posted before the receipt is prepared.'))
        # The people who run the weighbridge and the lab need not have purchasing rights; MOS groups authorise the step.
        po = self.sudo().purchase_id
        if po.state not in ('purchase', 'done'):
            raise UserError(self.env._('The purchase order must be confirmed.'))
        picking = po.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel') and p.picking_type_code == 'incoming')[:1]
        if not picking:
            raise UserError(self.env._('The purchase order has no open receipt.'))
        move = picking.move_ids.filtered(lambda m: m.product_id == insp.product_id)[:1]
        if not move:
            raise UserError(self.env._('The receipt has no line for %s.', insp.product_id.display_name))
        move.write({'quantity': insp.payable_qty, 'picked': True})
        self.sudo().write({'grn_picking_id': picking.id, 'grn_state': 'prepared'})
        self.env['mos.audit.event'].record('grn_prepared', self, f'{picking.name}: {insp.payable_qty} kg')
        return picking

    def action_confirm_grn(self):
        """The storekeeper confirms the prepared receipt; the buyer of the PO may not (segregation of duties)."""
        for visit in self:
            if visit.grn_state != 'prepared':
                raise UserError(self.env._('There is no prepared receipt to confirm.'))
            if visit.sudo().purchase_id.create_uid.id == self.env.uid:
                raise UserError(self.env._('The person who created the purchase order cannot confirm its goods receipt.'))
            picking = visit.grn_picking_id.sudo()
            result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._('The receipt needs attention in Inventory before it can be confirmed.'))
            visit.sudo().write({'grn_state': 'confirmed'})
            self.env['mos.audit.event'].record('grn_confirmed', visit, picking.name)

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        insp = self.inspection_id
        if self.purpose == 'inbound_po':
            if not insp or insp.state != 'decided':
                problems.append(self.env._('the inspection has no usage decision'))
            elif insp.decision != 'reject' and self.grn_state != 'confirmed':
                problems.append(self.env._('the goods receipt is not confirmed'))
            elif insp.decision == 'reject' and self.grn_state != 'rejected':
                problems.append(self.env._('the rejection has not been recorded'))
        return problems
