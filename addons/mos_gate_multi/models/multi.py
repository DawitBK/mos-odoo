from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

TOLERANCE_KG = 0.5


class MosGateWeighStep(models.Model):
    """A weight of the loaded truck taken between two drop-offs. The first step is the weight before unloading anything; each later one
    is the weight after one document's goods came off (FR-GATE-028)."""

    _name = 'mos.gate.weigh.step'
    _inherit = ['mos.audited']
    _description = 'MOS intermediate weighing'
    _order = 'visit_id, sequence, id'

    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    weight_kg = fields.Float(required=True, digits=(12, 1))
    note = fields.Char(help='Where the truck stood or what was unloaded.')
    by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    @api.constrains('weight_kg')
    def _check_weight(self):
        for step in self:
            if step.weight_kg <= 0:
                raise ValidationError(self.env._('A weight is above zero.'))


class MosGateVisitShare(models.Model):
    """One more purchase order the same truck is delivering against, with the weight that belongs to it. It gets its own receipt;
    the visit's own order receives what is left of the net weight (FR-GATE-028)."""

    _name = 'mos.gate.visit.share'
    _inherit = ['mos.audited']
    _description = 'MOS multi-document delivery line'
    _order = 'visit_id, sequence, id'

    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    purchase_id = fields.Many2one('purchase.order', required=True, domain="[('state', '=', 'purchase')]")
    product_id = fields.Many2one('product.product', required=True)
    partner_id = fields.Many2one(related='purchase_id.partner_id', store=True)
    allocated_kg = fields.Float(string='Weight (kg)', digits=(12, 1))
    grn_picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    grn_state = fields.Selection([('none', 'Not prepared'), ('prepared', 'Prepared'), ('confirmed', 'Confirmed')], default='none', readonly=True)

    _doc_once = models.Constraint('unique(visit_id, purchase_id, product_id)', 'This order and item are already on the visit.')

    @api.constrains('allocated_kg', 'purchase_id', 'product_id')
    def _check(self):
        for share in self:
            if share.allocated_kg < 0:
                raise ValidationError(self.env._('A weight cannot be negative.'))
            visit = share.visit_id.sudo()
            if visit.purchase_id and share.purchase_id == visit.purchase_id and share.product_id.id in visit.purchase_id.order_line.filtered(lambda l: l.product_id.type == 'consu').product_id.ids[:1]:
                raise ValidationError(self.env._('That is the visit\'s own order and item; it receives what is left over.'))
            if share.product_id not in share.purchase_id.sudo().order_line.product_id:
                raise ValidationError(self.env._('%(po)s has no line for %(product)s.', po=share.purchase_id.sudo().name, product=share.product_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        visits = self.env['mos.gate.visit'].browse([v['visit_id'] for v in vals_list if v.get('visit_id')])
        if any(v.state in ('cleared', 'exited', 'cancelled') for v in visits):
            raise UserError(self.env._('The visit is finished; documents cannot be added.'))
        return super().create(vals_list)

    def write(self, vals):
        if any(s.grn_state != 'none' for s in self) and {'allocated_kg', 'purchase_id', 'product_id'} & set(vals):
            raise UserError(self.env._('The receipt for this line has been prepared; the weight cannot change.'))
        return super().write(vals)

    def unlink(self):
        if any(s.grn_state != 'none' for s in self):
            raise UserError(self.env._('The receipt for this line has been prepared; it cannot be removed.'))
        return super().unlink()

    def _check_can_receive(self, po):
        """The same checks the visit's own order passes before its receipt: confirmed order, supplier standing, item in use, open period."""
        self.ensure_one()
        problems = []
        if po.state not in ('purchase', 'done'):
            problems.append(self.env._('the purchase order is not confirmed'))
        partner = po.partner_id
        if getattr(partner, 'mos_supplier_state', None) == 'blocked':
            problems.append(self.env._('the supplier is blocked'))
        elif self.env['ir.config_parameter'].sudo().get_bool('mos_procurement.enforce_asl', False) and getattr(partner, 'mos_supplier_state', 'approved') != 'approved':
            problems.append(self.env._('the supplier is not on the approved supplier list'))
        if not self.product_id.active:
            problems.append(self.env._('%s is archived', self.product_id.display_name))
        try:
            self.env['mos.period.lock'].check('inventory', fields.Date.context_today(self))
        except UserError as error:
            problems.append(str(error.args[0]))
        if problems:
            raise UserError(self.env._('The receipt for %(po)s cannot be prepared: %(why)s.', po=po.name, why='; '.join(problems)))

    def action_prepare(self):
        for share in self:
            if share.grn_picking_id:
                continue
            visit = share.visit_id
            if not visit.ticket_id or visit.ticket_id.state != 'posted':
                raise UserError(self.env._('The weigh ticket must be posted before the receipt is prepared.'))
            if share.allocated_kg <= 0:
                raise UserError(self.env._('Give the weight of %s first.', share.product_id.display_name))
            po = share.purchase_id.sudo()
            picking = po.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel') and p.picking_type_code == 'incoming')[:1]
            move = picking.move_ids.filtered(lambda m: m.product_id == share.product_id)[:1]
            if not move:
                raise UserError(self.env._('%(po)s has no open receipt line for %(product)s.', po=po.name, product=share.product_id.display_name))
            share._check_can_receive(po)
            move.write({'quantity': share.allocated_kg, 'picked': True})
            share.sudo().write({'grn_picking_id': picking.id, 'grn_state': 'prepared'})
            self.env['mos.audit.event'].record('share_grn_prepared', visit, f'{picking.name}: {share.allocated_kg} {share.product_id.display_name}')

    def action_confirm(self):
        for share in self:
            if share.grn_state != 'prepared':
                raise UserError(self.env._('There is no prepared receipt to confirm.'))
            if share.purchase_id.sudo().create_uid.id == self.env.uid:
                raise UserError(self.env._('The person who created the purchase order cannot confirm its goods receipt.'))
            result = share.grn_picking_id.sudo().with_context(skip_backorder=True).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._('The receipt needs attention in Inventory before it can be confirmed.'))
            share.sudo().write({'grn_state': 'confirmed'})
            self.env['mos.audit.event'].record('share_grn_confirmed', share.visit_id, share.grn_picking_id.name)


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    share_ids = fields.One2many('mos.gate.visit.share', 'visit_id', string='Other documents on this truck')
    step_ids = fields.One2many('mos.gate.weigh.step', 'visit_id', string='Intermediate weighings')
    shares_kg = fields.Float(compute='_compute_shares', digits=(12, 1), string='On other documents (kg)')
    primary_kg = fields.Float(compute='_compute_shares', digits=(12, 1), string="This visit's own order (kg)")

    @api.depends('share_ids.allocated_kg', 'net_weight')
    def _compute_shares(self):
        for visit in self:
            total = sum(visit.share_ids.mapped('allocated_kg'))
            visit.shares_kg = total
            visit.primary_kg = round(max(visit.net_weight - total, 0.0), 1) if visit.net_weight else 0.0

    def action_allocate_from_steps(self):
        """Weights of the loaded truck: before unloading, then after each document's goods came off. The difference between two
        weighings is that document's weight."""
        for visit in self:
            steps = visit.step_ids.sorted('sequence')
            shares = visit.share_ids.sorted('sequence')
            if len(steps) != len(shares) + 1:
                raise UserError(self.env._('Record one weighing before unloading and one after each of the %s other documents.', len(shares)))
            for index, share in enumerate(shares):
                dropped = steps[index].weight_kg - steps[index + 1].weight_kg
                if dropped <= 0:
                    raise UserError(self.env._('Weighing %(n)s is not lighter than the one before it.', n=index + 2))
                share.write({'allocated_kg': round(dropped, 1)})
            visit._check_allocation()

    def _check_allocation(self):
        for visit in self:
            if visit.net_weight and visit.shares_kg > visit.net_weight + TOLERANCE_KG:
                raise UserError(self.env._('The other documents add up to %(shares)s kg, more than the %(net)s kg on the weigh ticket.', shares=visit.shares_kg, net=visit.net_weight))

    def action_prepare_share_grns(self):
        for visit in self:
            visit._check_allocation()
            visit.share_ids.action_prepare()

    def action_confirm_share_grns(self):
        for visit in self:
            visit.share_ids.filtered(lambda s: s.grn_state == 'prepared').action_confirm()

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        if self.share_ids:
            if self.net_weight and self.primary_kg + self.shares_kg > self.net_weight + TOLERANCE_KG:
                problems.append(self.env._('the documents on this truck weigh more than the ticket'))
            pending = self.share_ids.filtered(lambda s: s.grn_state != 'confirmed')
            if pending:
                problems.append(self.env._('the receipt for %s is not confirmed', ', '.join(pending.mapped('purchase_id.name'))))
        return problems


class MosLabInspection(models.Model):
    """What other documents took off the truck is not part of the inspected load's payable quantity (FR-GATE-028)."""

    _inherit = 'mos.lab.inspection'

    @api.depends('net_weight', 'deduction_pct', 'decision', 'visit_id.received_qty', 'visit_id.flow', 'visit_id.weight_basis', 'visit_id.declared_qty', 'visit_id.bag_tare_kg', 'visit_id.share_ids.allocated_kg')
    def _compute_payable(self):
        super()._compute_payable()
        for insp in self:
            shares = insp.visit_id.shares_kg
            if shares and insp.decision != 'reject':
                insp.payable_qty = round(max(insp.payable_qty - shares * (1 - insp.deduction_pct / 100.0), 0.0), 1)
