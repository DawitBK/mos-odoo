from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError


class MosGateVisit(models.Model):
    """The three ways a delivery is received (FRD section 9):

    A  bulk material inspected before it is unloaded (the gate and weighbridge drive it);
    B  packaged material received into quarantine first and inspected afterwards;
    C  the client's own flow with the gate off: the lab opens the lot against the purchase order and the truck.
    """

    _inherit = 'mos.gate.visit'

    flow = fields.Selection(
        [('A', 'A: inspected before unloading'), ('B', 'B: packaged goods into quarantine'), ('C', 'C: lab-led, gate off')],
        default='A',
        required=True,
        tracking=True,
    )
    delivery_note = fields.Char(string="Supplier's delivery note")
    bags = fields.Integer()
    received_qty = fields.Float(string='Received quantity (kg)', digits=(14, 1), readonly=True, help='Flow B: what was counted in at the door.')
    receipt_exception_note = fields.Char(readonly=True)
    receipt_override_by_id = fields.Many2one('res.users', readonly=True)
    pv_draft_id = fields.Many2one('mos.pv.draft', readonly=True)

    # -- how each flow differs ------------------------------------------------------------------------------
    def _mos_inspection_before_exit(self):
        return self.flow != 'B'

    def _mos_requires_weighing(self):
        return self.flow != 'B'

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        if self.receipt_exception_note and not self.receipt_override_by_id:
            problems.append(self.env._('the receipt failed (%s): a supervisor must resolve it or override the exit', self.receipt_exception_note))
        if self.flow == 'B' and self.purpose == 'inbound_po' and self.grn_state not in ('confirmed', 'rejected'):
            problems.append(self.env._('the packaged goods have not been received into quarantine'))
        return problems

    def action_supervisor_override(self, reason):
        """A supervisor lets the truck go although the receipt is stuck; the exception stays open (FR-INB-005)."""
        user = self.env.user
        if not (user.has_group('mos_core.group_mos_admin') or user.has_group('mos_inbound.group_mos_storekeeper')):
            raise UserError(self.env._('Only a supervisor can override.'))
        if not reason:
            raise UserError(self.env._('Give the reason for the override.'))
        for visit in self:
            if not visit.receipt_exception_note:
                raise UserError(self.env._('There is nothing to override.'))
            visit.sudo().write({'receipt_override_by_id': user.id})
            self.env['mos.audit.event'].record('receipt_exit_override', visit, f'{visit.name}: {reason}')

    # -- pre-flight checks (FR-INB-004) ----------------------------------------------------------------------
    def _mos_preflight(self, product=None, qty=0.0):
        """Everything that would make the receipt fail later, found now. Returns the list of reasons."""
        self.ensure_one()
        problems = []
        order = self.sudo().purchase_id
        if order:
            if order.state not in ('purchase', 'done'):
                problems.append(self.env._('the purchase order is not confirmed'))
            partner = order.partner_id
            if partner.mos_supplier_state == 'blocked':
                problems.append(self.env._('the supplier is blocked'))
            elif self.env['ir.config_parameter'].sudo().get_bool('mos_procurement.enforce_asl') and partner.mos_supplier_state != 'approved':
                problems.append(self.env._('the supplier is not on the approved supplier list'))
            if product:
                lines = order.order_line.filtered(lambda line: line.product_id == product)
                if not lines:
                    problems.append(self.env._('the order has no line for %s', product.display_name))
                if not product.active:
                    problems.append(self.env._('%s is archived', product.display_name))
        try:
            self.env['mos.period.lock'].check('inventory', fields.Date.context_today(self))
        except UserError as error:
            problems.append(str(error.args[0]))
        silo = self.silo_id.sudo().fresh() if self.silo_id else False
        if silo:
            if not silo.active:
                problems.append(self.env._('the silo %s is not in use', silo.code))
            elif qty and qty > silo.free_kg + 0.0001:
                problems.append(self.env._('the silo %(silo)s has %(free)s kg free for a %(qty)s kg load', silo=silo.code, free=round(silo.free_kg), qty=round(qty)))
        return problems

    def action_check_preflight(self):
        self.ensure_one()
        problems = self._mos_preflight(self.inspection_id.product_id if self.inspection_id else None, self.inspection_id.payable_qty if self.inspection_id else 0.0)
        if problems:
            raise UserError(self.env._('Unloading cannot be authorised: %s.', '; '.join(problems)))
        return True

    def _mos_check_receipt(self, order, product, qty):
        super()._mos_check_receipt(order, product, qty)
        problems = self._mos_preflight(product, qty)
        if problems:
            raise UserError(self.env._('The receipt cannot be prepared: %s.', '; '.join(problems)))

    # -- automatic mode (FR-INB-002, 010) -----------------------------------------------------------------------
    def _mos_after_prepare(self):
        super()._mos_after_prepare()
        if self.env['mos.flow.setting'].mode('grn') == 'automatic':
            for visit in self:
                visit.with_user(SUPERUSER_ID).action_confirm_grn()
                self.env['mos.audit.event'].record('grn_auto_confirmed', visit, visit.name)

    def action_prepare_grn_safely(self):
        """Prepare the receipt; on failure keep the weighing, mark the visit and open an exception (FR-INB-005)."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                return self.action_prepare_grn()
        except UserError as error:
            message = str(error.args[0])
            self.sudo().write({'receipt_exception_note': message[:200]})
            self.env['mos.exception'].raise_for('receipt_exception', self, message[:200], severity='high', due_days=1)
            return False

    def _mos_after_confirm(self):
        super()._mos_after_confirm()
        for visit in self.filtered(lambda v: v.flow != 'B'):  # packaged goods are payable only once the lab has decided
            self.env['mos.pv.draft'].for_visit(visit)

    # -- flow B -------------------------------------------------------------------------------------------------
    def action_receive_quarantine(self, qty):
        """Flow B: the packaged goods are counted in and booked into quarantine; the lot to inspect opens at once."""
        self.ensure_one()
        if self.flow != 'B' or self.purpose != 'inbound_po':
            raise UserError(self.env._('Only an inbound visit in flow B is received into quarantine.'))
        if self.grn_state == 'confirmed':
            raise UserError(self.env._('This delivery is already received.'))
        if self.state not in ('arrived', 'weighing', 'weighed'):
            raise UserError(self.env._('The vehicle is not at the gate.'))
        if qty <= 0:
            raise UserError(self.env._('Enter what was counted in.'))
        order = self.sudo().purchase_id
        product = self._po_product()
        self._mos_check_receipt(order, product, qty)
        picking = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel') and p.picking_type_code == 'incoming')[:1]
        move = picking.move_ids.filtered(lambda m: m.product_id == product)[:1]
        if not move:
            raise UserError(self.env._('The purchase order has no open receipt for %s.', product.display_name))
        quarantine = self.env['mos.stock.status.change'].status_location(self.site_id, 'quarantine')
        picking.write({'location_dest_id': quarantine.id})
        move.write({'location_dest_id': quarantine.id, 'quantity': qty, 'picked': True})
        result = picking.with_context(skip_backorder=True).button_validate()
        if isinstance(result, dict):
            raise UserError(self.env._('The receipt needs attention in Inventory.'))
        self.sudo().write({'grn_picking_id': picking.id, 'grn_state': 'confirmed', 'received_qty': qty})
        self.env['mos.audit.event'].record('quarantine_received', self, f'{self.name}: {qty} {product.display_name} into {quarantine.display_name}')
        inspection = self.env['mos.lab.inspection'].sudo().create({'visit_id': self.id, 'product_id': product.id})
        self._mos_after_confirm()
        return inspection

    def _release_quarantine(self, inspection):
        """Flow B, after the usage decision: accepted goods become available, rejected goods are blocked."""
        self.ensure_one()
        qty = self.received_qty
        if not qty:
            return
        target_status = 'blocked' if inspection.decision == 'reject' else 'available'
        reason = self.env['mos.reason'].sudo().search([('domain', '=', 'status_change'), ('code', '=', 'FLOWB')], limit=1) or self.env['mos.reason'].sudo().create(
            {'domain': 'status_change', 'code': 'FLOWB', 'name': self.env._('Usage decision on received packaged goods')}
        )
        change = self.env['mos.stock.status.change'].sudo().create(
            {
                'site_id': self.site_id.id,
                'product_id': inspection.product_id.id,
                'qty': qty,
                'from_status': 'quarantine',
                'to_status': target_status,
                'location_id': self.silo_id.location_id.id if (self.silo_id and target_status == 'available') else False,
                'reason_id': reason.id,
                'note': f'{inspection.name}: {inspection.decision}',
            }
        )
        change.write({'state': 'approved', 'approved_by_id': inspection.decided_by_id.id})
        change.action_apply()
        if inspection.decision == 'reject':
            self.sudo().write({'grn_state': 'rejected'})
        self.env['mos.audit.event'].record('quarantine_released', self, f'{self.name}: {qty} -> {target_status}')

    # -- flow C -------------------------------------------------------------------------------------------------
    @api.model
    def lab_intake(self, order, plate, gross, tare, driver_name='', delivery_note='', bags=0, silo=None):
        """Flow C: the lab opens the lot against the purchase order and the truck, with the weights it was given."""
        if order.state not in ('purchase', 'done'):
            raise UserError(self.env._('The purchase order must be confirmed.'))
        if gross <= tare or tare < 0:
            raise UserError(self.env._('The gross weight must be above the tare.'))
        site = self.env['mos.site'].sudo().search([('warehouse_id', '=', order.picking_type_id.warehouse_id.id)], limit=1)
        if not site:
            raise UserError(self.env._('No MOS site uses the warehouse of this order.'))
        visit = self.sudo().create(
            {
                'site_id': site.id,
                'plate': plate,
                'purpose': 'inbound_po',
                'flow': 'C',
                'purchase_id': order.id,
                'partner_id': order.partner_id.id,
                'driver_name': driver_name,
                'delivery_note': delivery_note,
                'bags': bags,
                'silo_id': silo.id if silo else False,
            }
        )
        user = self.env.user
        ticket = self.env['mos.weigh.ticket'].sudo().create(
            {
                'visit_id': visit.id,
                'first_weight': gross,
                'second_weight': tare,
                'first_by_id': user.id,
                'second_by_id': user.id,
                'verified_by_id': user.id,
                'provisional': True,
                'state': 'posted',
            }
        )
        visit.sudo().write({'state': 'weighed'})
        self.env['mos.audit.event'].record('lab_intake', visit, f'{visit.name}: {ticket.name}, gross {gross}, tare {tare} entered by the lab')
        inspection = self.env['mos.lab.inspection'].sudo().create({'visit_id': visit.id, 'product_id': visit._po_product().id})
        return visit, inspection


class MosWeighTicket(models.Model):
    _inherit = 'mos.weigh.ticket'

    provisional = fields.Boolean(readonly=True, help='Entered by the lab from the delivery note and a stored tare, not weighed on the bridge here.')


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    @api.depends('net_weight', 'deduction_pct', 'decision', 'visit_id.received_qty', 'visit_id.flow', 'visit_id.weight_basis', 'visit_id.declared_qty')
    def _compute_payable(self):
        for insp in self:
            if insp.decision == 'reject':
                insp.payable_qty = 0.0
            else:
                visit = insp.visit_id
                if visit.flow == 'B' and visit.received_qty:
                    base = visit.received_qty
                elif visit.weight_basis == 'declared' and visit.declared_qty:
                    base = visit.declared_qty  # the weight the two sides agreed on (FRD 9.5 #11)
                else:
                    base = insp.net_weight
                insp.payable_qty = round(base * (1 - insp.deduction_pct / 100.0), 1)

    def decide(self, decision, deduction_pct=0.0, note=''):
        result = super().decide(decision, deduction_pct, note)
        for insp in self:
            if insp.visit_id.flow == 'B':
                insp.visit_id.sudo()._release_quarantine(insp)
                if insp.decision != 'reject':
                    self.env['mos.pv.draft'].for_visit(insp.visit_id)
        return result
