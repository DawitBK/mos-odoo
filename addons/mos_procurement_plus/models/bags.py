from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosBagType(models.Model):
    """What an empty bag weighs, so a load of bagged goods is paid net of its sacks (FR-PRC-021)."""

    _name = 'mos.bag.type'
    _inherit = ['mos.audited']
    _description = 'MOS bag type'
    _order = 'name'

    name = fields.Char(required=True)
    tare_kg = fields.Float(string='Empty bag (kg)', required=True, digits=(8, 3))
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint('unique(name)', 'This bag type already exists.')

    @api.constrains('tare_kg')
    def _check(self):
        for bag in self:
            if bag.tare_kg < 0:
                raise ValidationError(self.env._('A bag cannot weigh less than nothing.'))


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    bag_type_id = fields.Many2one('mos.bag.type')
    bag_count = fields.Integer(string='Bags counted')
    bag_tare_kg = fields.Float(string='Bag tare (kg)', compute='_compute_bag_tare', store=True, digits=(12, 1))

    @api.depends('bag_type_id.tare_kg', 'bag_count')
    def _compute_bag_tare(self):
        for visit in self:
            visit.bag_tare_kg = round(visit.bag_count * visit.bag_type_id.tare_kg, 1) if visit.bag_type_id else 0.0


class MosLabInspection(models.Model):
    """Bagged goods are received and paid net of the bags (FR-PRC-021)."""

    _inherit = 'mos.lab.inspection'

    @api.depends('net_weight', 'deduction_pct', 'decision', 'visit_id.received_qty', 'visit_id.flow', 'visit_id.weight_basis', 'visit_id.declared_qty', 'visit_id.bag_tare_kg')
    def _compute_payable(self):
        super()._compute_payable()
        for insp in self:
            tare = insp.visit_id.bag_tare_kg
            if tare and insp.decision != 'reject':
                insp.payable_qty = round(max(insp.payable_qty - tare * (1 - insp.deduction_pct / 100.0), 0.0), 1)


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    mos_service = fields.Boolean(compute='_compute_mos_service', store=True)
    mos_service_receipt_ids = fields.One2many('mos.service.receipt', 'line_id')

    @api.depends('product_id.type')
    def _compute_mos_service(self):
        for line in self:
            line.mos_service = line.product_id.type == 'service'


class MosServiceReceipt(models.Model):
    """Confirmation that a service was done or a non-stock item arrived, by the person who asked for it or the buyer (FR-PRC-022)."""

    _name = 'mos.service.receipt'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS service receipt'
    _order = 'id desc'
    _mos_final_states = ('confirmed',)

    name = fields.Char(readonly=True, copy=False, default='New')
    line_id = fields.Many2one('purchase.order.line', required=True, ondelete='restrict', domain="[('order_id.state', '=', 'purchase')]")
    order_id = fields.Many2one(related='line_id.order_id', store=True)
    product_id = fields.Many2one(related='line_id.product_id', store=True)
    qty = fields.Float(required=True, digits=(14, 2))
    note = fields.Char(string='What was done', required=True)
    confirmed_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([('confirmed', 'Confirmed')], default='confirmed', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        for vals in vals_list:
            line = self.env['purchase.order.line'].sudo().browse(vals['line_id'])
            if vals.get('qty', 0) <= 0:
                raise ValidationError(self.env._('Confirm a quantity above zero.'))
            if line.product_id.type == 'consu' and line.product_id.is_storable:
                raise UserError(self.env._('Stock items are received at the gate, not here.'))
            requester = line.order_id.mos_request_id.requester_id
            if not (user == requester or user == line.order_id.user_id or user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
                raise UserError(self.env._('Only the person who asked for it, the buyer or a procurement manager can confirm this.'))
            already = sum(self.sudo().search([('line_id', '=', line.id)]).mapped('qty'))
            if already + vals['qty'] > line.product_qty + 0.0001:
                raise UserError(self.env._('Only %(left)s is still open on that line.', left=line.product_qty - already))
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.service.receipt')
        receipts = super().create(vals_list)
        for rec in receipts:
            line = rec.line_id.sudo()
            line.write({'qty_received_method': 'manual', 'qty_received_manual': sum(line.mos_service_receipt_ids.mapped('qty'))})
            self.env['mos.audit.event'].record('service_received', rec, f'{rec.order_id.sudo().name}: {rec.qty} {rec.product_id.sudo().display_name}: {rec.note}')
        return receipts
