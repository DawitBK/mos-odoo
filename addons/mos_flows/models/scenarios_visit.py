from odoo import api, fields, models
from odoo.exceptions import UserError


def _has(user, *groups):
    return any(user.has_group(g) for g in groups)


class MosGateVisit(models.Model):
    """The failure and exception cases of a delivery (FRD 9.5): no purchase order, wrong order, abandoned truck, exit without
    tare, weight disputes, reversal of a receipt. Each is an explicit, audited act by the right person."""

    _inherit = 'mos.gate.visit'

    state = fields.Selection(selection_add=[('abandoned', 'Abandoned')], ondelete={'abandoned': 'set default'})
    declared_qty = fields.Float(string="Declared quantity (kg)", digits=(14, 1), help="What the supplier's delivery note says.")
    weight_basis = fields.Selection([('bridge', 'Bridge weight'), ('declared', 'Declared weight')], default='bridge', required=True)
    weight_variance = fields.Float(string='Bridge minus declared (kg)', digits=(14, 1), compute='_compute_weight_variance', store=True)
    weight_dispute_note = fields.Char(readonly=True)
    tare_estimated = fields.Boolean(readonly=True, help='The truck left without a second weighing; the tare is an estimate.')
    payment_blocked = fields.Boolean(readonly=True, help='No payment until a manager has accepted the estimated tare.')

    @api.depends('net_weight', 'declared_qty')
    def _compute_weight_variance(self):
        for visit in self:
            visit.weight_variance = visit.net_weight - visit.declared_qty if visit.declared_qty else 0.0

    @api.model
    def _mos_strict(self):
        """The controls that need a supervisor's hand (PO before weighing, tare and re-weigh confirmation)."""
        return self.env['ir.config_parameter'].sudo().get_bool('mos_flows.strict_controls')

    def action_start_weighing(self):
        if self._mos_strict():
            for visit in self:
                if visit.purpose == 'inbound_po' and visit.flow in ('A', 'C') and not visit.purchase_id:
                    raise UserError(self.env._('The visit is on hold: no purchase order. A buyer must link one, or the truck is rejected at the gate. Nothing is unloaded without an order.'))
        return super().action_start_weighing()

    # -- 1, 2: no purchase order, wrong purchase order ---------------------------------------------------------------
    def action_link_po(self, order, reason):
        """Link or change the purchase order before the receipt is posted; the inspection re-resolves its specification (#1, #2)."""
        user = self.env.user
        if not _has(user, 'mos_procurement.group_mos_procurement_user', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a buyer can link a purchase order.'))
        if not reason:
            raise UserError(self.env._('Give the reason for the link.'))
        if order.state not in ('purchase', 'done'):
            raise UserError(self.env._('The purchase order must be confirmed.'))
        for visit in self:
            if visit.grn_state in ('confirmed', 'rejected') or visit.state in ('exited', 'cancelled', 'abandoned'):
                raise UserError(self.env._('The receipt is already posted or the visit is closed; use a receipt reassignment instead.'))
            old = visit.sudo().purchase_id
            visit.sudo().write({'purchase_id': order.id, 'partner_id': order.partner_id.id})
            product = visit._po_product()
            for inspection in visit.inspection_ids:
                if inspection.state == 'decided':
                    if inspection.product_id != product:
                        raise UserError(self.env._('The inspection is decided for another product: reopen the decision first.'))
                    continue
                if inspection.product_id != product:
                    spec = self.env['mos.lab.spec'].in_force(product)
                    if not spec:
                        raise UserError(self.env._('There is no approved specification for %s.', product.display_name))
                    inspection.sudo().write({'product_id': product.id, 'spec_id': spec.id, 'state': 'open'})
                    inspection.result_ids.filtered('valid').sudo().write({'valid': False, 'invalid_cause': self.env._('Purchase order changed to %s', order.name)})
            self.env['mos.audit.event'].record('po_linked', visit, f'{visit.name}: {old.name or "none"} -> {order.name}: {reason}')

    def action_reject_at_gate(self, reason):
        """The truck is turned away at the gate with a rejection note (#1, #8)."""
        if not reason:
            raise UserError(self.env._('Give the reason for the rejection.'))
        for visit in self:
            if visit.state not in ('expected', 'arrived', 'weighing', 'weighed'):
                raise UserError(self.env._('Only a truck still at the gate can be rejected.'))
            self.env['mos.gate.incident'].sudo().create({'site_id': visit.site_id.id, 'visit_id': visit.id, 'kind': 'rejection', 'severity': 'medium', 'description': reason})
            visit.sudo().write({'state': 'cancelled'})
            self.env['mos.audit.event'].record('rejected_at_gate', visit, f'{visit.name}: {reason}')

    # -- 6, 7: leaves without second weighing, leaves without unloading -----------------------------------------------
    def _mos_require_supervisor(self):
        if not _has(self.env.user, 'mos_flows.group_mos_gate_supervisor', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a gate supervisor can do this.'))

    def action_exit_without_tare(self, reason, estimated_tare):
        """The truck left before its second weighing: record the exit, estimate the tare, flag the receipt and block payment (#6)."""
        self._mos_require_supervisor()
        if not reason or estimated_tare <= 0:
            raise UserError(self.env._('Give the reason and the estimated tare.'))
        for visit in self:
            ticket = visit.ticket_id
            if visit.state != 'weighing' or not ticket or ticket.state != 'draft' or not ticket.first_weight or ticket.second_weight:
                raise UserError(self.env._('Only a truck with a first weighing and no second can leave without tare.'))
            if visit.direction_is_out() or estimated_tare >= ticket.first_weight:
                raise UserError(self.env._('The estimated tare must be below the gross weight of an inbound truck.'))
            user = self.env.user
            ticket.sudo().write(
                {'second_weight': estimated_tare, 'second_by_id': user.id, 'verified_by_id': user.id, 'provisional': True, 'state': 'posted'}
            )
            visit.sudo().write({'state': 'exited', 'exited_at': fields.Datetime.now(), 'tare_estimated': True, 'payment_blocked': True})
            self.env['mos.audit.event'].record('exit_without_tare', visit, f'{visit.name}: estimated tare {estimated_tare}: {reason}')
            self.env['mos.exception'].raise_for('receipt_exception', visit, self.env._('%s left without a second weighing (tare estimated)', visit.name), severity='high', due_days=1)

    def direction_is_out(self):
        self.ensure_one()
        return (self.purpose or '').startswith('outbound')

    def action_release_payment_block(self, reason):
        """A procurement manager accepts the estimated tare; payment may go ahead (#6)."""
        if not _has(self.env.user, 'mos_procurement.group_mos_procurement_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a procurement manager can release the payment block.'))
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        for visit in self:
            visit.sudo().write({'payment_blocked': False})
            self.env['mos.audit.event'].record('payment_block_released', visit, f'{visit.name}: {reason}')
            visit._rematch_held_invoices()

    def action_abandon(self, reason):
        """An accepted truck left without unloading: the visit is abandoned, open inspections close, samples stay (#7)."""
        self._mos_require_supervisor()
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        for visit in self:
            if visit.state not in ('weighing', 'weighed', 'cleared') or visit.grn_state != 'none':
                raise UserError(self.env._('Only a truck that is still waiting to unload can be marked abandoned.'))
            visit.inspection_ids.filtered(lambda i: i.state != 'decided').sudo().write({'state': 'closed'})
            visit.sudo().write({'state': 'abandoned', 'exited_at': fields.Datetime.now()})
            self.env['mos.gate.incident'].sudo().create({'site_id': visit.site_id.id, 'visit_id': visit.id, 'kind': 'other', 'severity': 'medium', 'description': self.env._('Abandoned: %s', reason)})
            self.env['mos.audit.event'].record('visit_abandoned', visit, f'{visit.name}: {reason}')

    # -- 11: weight dispute ------------------------------------------------------------------------------------------
    def action_resolve_weight_dispute(self, basis, note):
        """Agree which weight the receipt and payment use; the variance stays on record (#11)."""
        if not _has(self.env.user, 'mos_procurement.group_mos_procurement_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a procurement manager can settle a weight dispute.'))
        if basis not in ('bridge', 'declared') or not note:
            raise UserError(self.env._('Choose the weight basis and say how it was agreed.'))
        for visit in self:
            if basis == 'declared' and not visit.declared_qty:
                raise UserError(self.env._('There is no declared quantity to use.'))
            if visit.grn_state in ('confirmed', 'rejected'):
                raise UserError(self.env._('The receipt is already posted: raise a quantity correction instead.'))
            visit.sudo().write({'weight_basis': basis, 'weight_dispute_note': note})
            self.env['mos.audit.event'].record('weight_dispute_settled', visit, f'{visit.name}: {basis}, variance {visit.weight_variance}: {note}')

    # -- 3, 32: reverse a posted receipt ----------------------------------------------------------------------------
    def action_reverse_grn(self, reason):
        """Undo a posted receipt that nothing depends on (#3, #32). Matched or paid invoices must be reversed first."""
        if not _has(self.env.user, 'mos_procurement.group_mos_procurement_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a procurement manager can reverse a receipt.'))
        if not reason:
            raise UserError(self.env._('Give the reason for the reversal.'))
        for visit in self:
            if visit.grn_state != 'confirmed' or not visit.grn_picking_id:
                raise UserError(self.env._('There is no posted receipt to reverse.'))
            order = visit.sudo().purchase_id
            invoices = self.env['mos.supplier.invoice'].sudo().search([('purchase_id', '=', order.id), ('state', 'in', ('payable', 'vouchered', 'paid'))])
            if invoices:
                raise UserError(
                    self.env._(
                        'The order has matched or paid invoices (%s): reverse the invoice first, or use a supplier return and a debit note.',
                        ', '.join(invoices.mapped('name')),
                    )
                )
            picking = visit.grn_picking_id.sudo()
            back = picking._create_return()
            back.action_return_all()
            back.action_confirm()
            for move in back.move_ids:
                move.write({'quantity': move.product_uom_qty, 'picked': True})
            result = back.with_context(skip_backorder=True).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._('The return transfer needs attention in Inventory.'))
            visit.sudo().write({'grn_picking_id': False, 'grn_state': 'none', 'received_qty': 0.0})
            if visit.pv_draft_id:
                visit.pv_draft_id.sudo().action_reverse()
            self.env['mos.audit.event'].record('grn_reversed', visit, f'{visit.name}: {picking.name} reversed: {reason}')

    # -- helpers used by several scenarios ------------------------------------------------------------------------
    def _rematch_held_invoices(self):
        """Invoices that waited for a receipt or a decision are matched again now."""
        for visit in self:
            held = self.env['mos.supplier.invoice'].sudo().search(
                [('purchase_id', '=', visit.sudo().purchase_id.id), ('match_state', 'in', ('awaiting_receipt', 'qc_hold', 'payment_hold')), ('state', '=', 'draft')]
            )
            if held:
                held.action_match()

    def _close_short_if_within_tolerance(self):
        """Under-delivery: the line stays open, and closes when what is owed is within the tolerance (#14)."""
        for visit in self:
            order = visit.sudo().purchase_id
            for line in order.order_line.filtered(lambda l: l.product_id.is_storable):
                policy = self.env['mos.receipt.policy'].sudo().for_product(line.product_id)
                if not policy or not line.product_qty:
                    continue
                remaining = line.product_qty - line.qty_received
                if 0 < remaining <= line.product_qty * policy.under_pct / 100.0:
                    pending = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel') and p.picking_type_code == 'incoming')
                    pending.move_ids.filtered(lambda m: m.product_id == line.product_id).sudo()._action_cancel()
                    order.message_post(body=self.env._('%(product)s closed short: %(left)s kg was still owed, within the %(pct)s%% tolerance.', product=line.product_id.display_name, left=round(remaining, 1), pct=policy.under_pct))

    def _mos_after_confirm(self):
        super()._mos_after_confirm()
        self._close_short_if_within_tolerance()
        self._rematch_held_invoices()
