from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.mos_core.units import totals_html


class MosStockReceipt(models.Model):
    """Stock that arrives without a purchase, a transfer, a production run or a return: a miscellaneous receipt (with a reason) or the
    opening balance at go-live (FR-INV-005, FR-PLT-047). Both carry an explicit cost, because nothing else says what the goods are worth,
    and both are restricted to inventory managers, with the matrix deciding whether an approval is needed.
    """

    _name = 'mos.stock.receipt'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS miscellaneous receipt'
    _order = 'id desc'
    _mos_final_states = ('received', 'rejected', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state', 'received_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    kind = fields.Selection([('misc', 'Miscellaneous receipt'), ('opening', 'Opening balance')], default='misc', required=True, tracking=True)
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user._mos_current_site())
    location_id = fields.Many2one('stock.location', string='Receive into', required=True, domain="[('usage', '=', 'internal')]")
    reason_id = fields.Many2one('mos.reason', domain=[('domain', '=', 'misc_receipt')])
    note = fields.Char(string='Comments')
    date = fields.Date(default=fields.Date.context_today, required=True)
    line_ids = fields.One2many('mos.stock.receipt.line', 'receipt_id', string='Products')
    amount = fields.Float(compute='_compute_totals', store=True, string='Value')
    item_count = fields.Integer(compute='_compute_totals', string='Total items')
    totals = fields.Html(compute='_compute_totals', sanitize=False, string='Total quantity')
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    received_by_id = fields.Many2one('res.users', readonly=True)
    approved_by_id = fields.Many2one('res.users', compute='_compute_approved_by', string='Approved by')
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('received', 'Received'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.depends('line_ids.qty', 'line_ids.product_id', 'line_ids.value')
    def _compute_totals(self):
        for receipt in self:
            per_unit = {}
            for line in receipt.line_ids:
                unit = line.product_id.uom_id.name or ''
                per_unit[unit] = per_unit.get(unit, 0.0) + line.qty
            receipt.item_count = len(receipt.line_ids)
            receipt.totals = totals_html(per_unit)
            receipt.amount = sum(receipt.line_ids.mapped('value'))

    @api.depends('mos_approval_request_id.step_ids.decided_by_id')
    def _compute_approved_by(self):
        for receipt in self:
            steps = receipt.mos_approval_request_id.sudo().step_ids.filtered('decided_by_id').sorted('decided_at')
            receipt.approved_by_id = steps[-1:].decided_by_id

    @api.constrains('kind', 'reason_id')
    def _check_reason(self):
        for receipt in self:
            if receipt.kind == 'misc' and not receipt.reason_id:
                raise ValidationError(self.env._('A miscellaneous receipt needs a reason.'))

    @api.constrains('date')
    def _check_date(self):
        for receipt in self:
            if receipt.date > fields.Date.context_today(self):
                raise ValidationError(self.env._('A receipt cannot be dated in the future.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.stock.receipt')
        return super().create(vals_list)

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.amount

    def _mos_approval_site(self):
        return self.site_id

    def _mos_approval_done(self, request):
        self.sudo().write({'mos_approval_state': 'approved', 'state': 'approved'})

    def _mos_approval_closed(self, request, state):
        if state != 'approved':
            self.sudo().write({'mos_approval_state': state, 'state': 'draft' if state in ('returned', 'cancelled') else 'rejected'})

    def action_submit(self):
        dialog = self._mos_attestation_dialog('action_submit')
        if dialog:
            return dialog
        for receipt in self:
            if receipt.state != 'draft':
                raise UserError(self.env._('Only a draft receipt can be submitted.'))
            if not receipt.line_ids:
                raise UserError(self.env._('List at least one product.'))
            if receipt._mos_approval_required():
                receipt.action_mos_submit_approval()
                receipt.write({'state': 'pending'})
            else:
                receipt.write({'state': 'approved', 'mos_approval_state': 'approved'})

    def action_receive(self):
        for receipt in self:
            if receipt.state != 'approved':
                raise UserError(self.env._('Only an approved receipt can be posted to stock.'))
            if receipt.requested_by_id == self.env.user and not receipt.mos_approval_request_id:
                raise UserError(self.env._('A receipt that needed no approval must be posted by someone other than its requester.'))
            source = self.env.ref('mos_inventory.location_misc_receipts').sudo()
            for line in receipt.line_ids:
                receipt._move(line, source)
            receipt.write({'state': 'received', 'received_by_id': self.env.uid})
            self.env['mos.audit.event'].record('stock_received', receipt, f'{receipt.name}: {len(receipt.line_ids)} products, value {receipt.amount:.2f}, {receipt.kind}')

    def action_cancel(self):
        for receipt in self:
            if receipt.state in ('received', 'rejected', 'cancelled'):
                raise UserError(self.env._('This receipt is closed.'))
            if receipt.state == 'pending':
                receipt.action_mos_recall_approval()
            receipt.write({'state': 'cancelled'})

    def _move(self, line, source):
        product = line.product_id
        move = self.env['stock.move'].sudo().create(
            {
                'product_id': product.id,
                'product_uom_qty': line.qty,
                'uom_id': product.uom_id.id,
                'location_id': source.id,
                'location_dest_id': self.location_id.id,
                'origin': self.name,
                'price_unit': line.unit_cost,
            }
        )
        move._action_confirm()
        move._action_assign()
        if line.lot_name and product.tracking != 'none':
            lot = self.env['stock.lot'].sudo().create({'name': line.lot_name, 'product_id': product.id, 'mos_mfg_date': line.mfg_date or False, 'mos_expiry_date': line.expiry_date or False})
            move.move_line_ids.write({'lot_id': lot.id, 'quantity': line.qty})
        else:
            move.write({'quantity': line.qty})
        move.write({'picked': True})
        move._action_done()
        return move


class MosStockReceiptLine(models.Model):
    _name = 'mos.stock.receipt.line'
    _description = 'MOS miscellaneous receipt line'

    receipt_id = fields.Many2one('mos.stock.receipt', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('is_storable', '=', True)]")
    code = fields.Char(related='product_id.default_code', string='Item code')
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unit')
    qty = fields.Float(string='Quantity', required=True, digits=(14, 3))
    unit_cost = fields.Float(string='Cost per unit', required=True, digits=(14, 4))
    value = fields.Float(compute='_compute_value', store=True, string='Value')
    lot_name = fields.Char(string='Lot')
    mfg_date = fields.Date(string='Made on')
    expiry_date = fields.Date(string='Expires on')

    @api.constrains('qty', 'unit_cost')
    def _check_amounts(self):
        for line in self:
            if line.qty <= 0:
                raise ValidationError(self.env._('The quantity must be above zero.'))
            if line.unit_cost <= 0:
                raise ValidationError(self.env._('Give the cost per unit: a receipt without a cost would put goods in stock at no value.'))

    @api.constrains('mfg_date', 'expiry_date')
    def _check_dates(self):
        for line in self:
            if line.mfg_date and line.expiry_date and line.expiry_date < line.mfg_date:
                raise ValidationError(self.env._('The expiry date is before the date made.'))

    @api.depends('qty', 'unit_cost')
    def _compute_value(self):
        for line in self:
            line.value = line.qty * line.unit_cost
