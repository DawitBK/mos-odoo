from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosPurchaseRequest(models.Model):
    """What a department asks the buyers to purchase. It goes through the approval matrix and then becomes a
    purchase order for an approved supplier (FR-PRC-020..024)."""

    _name = 'mos.purchase.request'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS purchase request'
    _order = 'id desc'
    _mos_final_states = ('converted', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state', 'order_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    requester_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    department = fields.Char()
    needed_by = fields.Date()
    reason = fields.Text(required=True)
    line_ids = fields.One2many('mos.purchase.request.line', 'request_id', string='Items')
    amount = fields.Float(compute='_compute_amount', store=True, string='Estimated cost')
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('converted', 'Ordered'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )
    order_id = fields.Many2one('purchase.order', readonly=True, copy=False)

    @api.depends('line_ids.qty', 'line_ids.estimated_price')
    def _compute_amount(self):
        for req in self:
            req.amount = sum(line.qty * line.estimated_price for line in req.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.purchase.request')
        return super().create(vals_list)

    # approvals --------------------------------------------------------------------------------------------
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
        for req in self:
            if req.state != 'draft':
                raise UserError(self.env._('Only a draft request can be submitted.'))
            if not req.line_ids:
                raise UserError(self.env._('List at least one item.'))
            if req._mos_approval_required():
                req.action_mos_submit_approval()
                req.write({'state': 'pending'})
            else:
                req.write({'state': 'approved', 'mos_approval_state': 'approved'})

    def action_convert(self, supplier):
        """Turn the approved request into a purchase order for ``supplier`` (a buyer's act)."""
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(self.env._('Only an approved request can be ordered.'))
        if self.env['ir.config_parameter'].sudo().get_bool('mos_procurement.enforce_asl') and supplier.mos_supplier_state != 'approved':
            raise UserError(self.env._('%s is not on the approved supplier list.', supplier.display_name))
        order = self.env['purchase.order'].create(
            {
                'partner_id': supplier.id,
                'origin': self.name,
                'mos_request_id': self.id,
                'order_line': [
                    Command.create({'product_id': line.product_id.id, 'product_qty': line.qty, 'price_unit': line.estimated_price, 'name': line.product_id.display_name})
                    for line in self.line_ids
                ],
            }
        )
        self.write({'state': 'converted', 'order_id': order.id})
        self.env['mos.audit.event'].record('request_ordered', self, f'{self.name} -> {order.name}')
        return order

    def action_cancel(self):
        for req in self:
            if req.state in ('converted', 'cancelled'):
                raise UserError(self.env._('This request is closed.'))
            if req.state == 'pending':
                req.action_mos_recall_approval()
            req.write({'state': 'cancelled'})


class MosPurchaseRequestLine(models.Model):
    _name = 'mos.purchase.request.line'
    _description = 'MOS purchase request line'

    request_id = fields.Many2one('mos.purchase.request', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('purchase_ok', '=', True)]")
    qty = fields.Float(required=True, default=1.0, digits=(14, 1))
    uom_id = fields.Many2one(related='product_id.uom_id')
    estimated_price = fields.Float(digits=(12, 4))

    @api.constrains('qty', 'estimated_price')
    def _check_values(self):
        for line in self:
            if line.qty <= 0 or line.estimated_price < 0:
                raise ValidationError(self.env._('Quantities must be above zero and prices cannot be negative.'))

    @api.onchange('product_id')
    def _onchange_product(self):
        if self.product_id and not self.estimated_price:
            self.estimated_price = self.product_id.standard_price

    def _guard(self):
        if any(line.request_id.state != 'draft' for line in self):
            raise UserError(self.env._('Items of a submitted request cannot change; recall it first.'))

    @api.model_create_multi
    def create(self, vals_list):
        requests = self.env['mos.purchase.request'].browse([v['request_id'] for v in vals_list if v.get('request_id')])
        if any(r.state != 'draft' for r in requests):
            raise UserError(self.env._('Items of a submitted request cannot change; recall it first.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard()
        return super().write(vals)

    def unlink(self):
        self._guard()
        return super().unlink()


class MosSourcingPolicy(models.Model):
    """From what order value the buyers must collect competing quotes (FR-PRC-014)."""

    _name = 'mos.sourcing.policy'
    _inherit = ['mos.audited']
    _description = 'MOS sourcing policy'
    _order = 'amount_from'

    amount_from = fields.Float(required=True, help='Orders from this value need the quotes below.')
    min_quotes = fields.Integer(required=True, default=3)
    site_id = fields.Many2one('mos.site', help='Empty applies to every site.')
    active = fields.Boolean(default=True)

    @api.constrains('amount_from', 'min_quotes')
    def _check(self):
        for policy in self:
            if policy.amount_from < 0 or policy.min_quotes < 1:
                raise ValidationError(self.env._('A policy needs a non-negative amount and at least one quote.'))

    @api.model
    def quotes_needed(self, amount, site=None):
        rows = self.search([]).filtered(lambda p: p.amount_from <= amount and (not p.site_id or p.site_id == site))
        return max(rows.mapped('min_quotes') or [0])


class MosPurchaseQuote(models.Model):
    _name = 'mos.purchase.quote'
    _inherit = ['mos.audited']
    _description = 'MOS supplier quote'

    order_id = fields.Many2one('purchase.order', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', required=True, domain="[('supplier_rank', '>', 0)]")
    amount = fields.Float(required=True)
    valid_until = fields.Date()
    note = fields.Char()

    _quote_uniq = models.Constraint('unique(order_id, partner_id)', 'A supplier can give only one quote per order.')

    @api.constrains('amount')
    def _check_amount(self):
        for quote in self:
            if quote.amount <= 0:
                raise ValidationError(self.env._('A quote must be above zero.'))


class MosReceiptPolicy(models.Model):
    """How much more than ordered the gate may receive. A row without a product is the default (FR-PRC-016)."""

    _name = 'mos.receipt.policy'
    _inherit = ['mos.audited']
    _description = 'MOS receipt tolerance'
    _order = 'product_id'

    product_id = fields.Many2one('product.product', help='Empty is the default for every product.')
    over_pct = fields.Float(string='Excess allowed (%)', default=2.0)
    over_action = fields.Selection([('block', 'Refuse the excess'), ('approve', 'Accept with a manager\'s approval'), ('warn', 'Accept and flag')], default='block', required=True)
    under_pct = fields.Float(string='Short delivery that closes the line (%)', default=5.0, help='When what is still owed is within this share of the order, the rest is closed.')

    @api.constrains('over_pct', 'under_pct')
    def _check(self):
        for policy in self:
            if policy.over_pct < 0 or policy.under_pct < 0:
                raise ValidationError(self.env._('The tolerances cannot be negative.'))

    _product_uniq = models.Constraint('unique(product_id)', 'This product already has a receipt policy.')

    @api.model
    def for_product(self, product):
        policy = self.search([('product_id', '=', product.id)], limit=1) or self.search([('product_id', '=', False)], limit=1)
        return policy


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    mos_request_id = fields.Many2one('mos.purchase.request', string='Purchase request', readonly=True, copy=False)
    mos_quote_ids = fields.One2many('mos.purchase.quote', 'order_id', string='Quotes')
    mos_quote_count = fields.Integer(compute='_compute_quote_count')
    mos_sole_source_reason = fields.Char(string='Why no competing quotes')
    mos_amendment_ids = fields.One2many('mos.po.amendment', 'purchase_id')

    @api.depends('mos_quote_ids')
    def _compute_quote_count(self):
        for order in self:
            order.mos_quote_count = len(order.mos_quote_ids)

    def _mos_site_of(self):
        self.ensure_one()
        return self.env['mos.site'].sudo().search([('warehouse_id', '=', self.picking_type_id.warehouse_id.id)], limit=1)

    def button_confirm(self):
        enforce = self.env['ir.config_parameter'].sudo().get_bool('mos_procurement.enforce_asl')
        for order in self.filtered(lambda o: o.state in ('draft', 'sent')):
            if enforce and order.partner_id.mos_supplier_state != 'approved':
                raise UserError(self.env._('%s is not on the approved supplier list.', order.partner_id.display_name))
            needed = self.env['mos.sourcing.policy'].quotes_needed(order.amount_total, order._mos_site_of())
            if needed and not order.mos_sole_source_reason:
                quoted = order.mos_quote_ids
                if len(quoted) < needed:
                    raise UserError(self.env._('This order needs %(need)s competing quotes and has %(have)s. Add them or record why there are none.', need=needed, have=len(quoted)))
                if order.partner_id not in quoted.partner_id:
                    raise UserError(self.env._('The chosen supplier must be one of the quoted suppliers.'))
        return super().button_confirm()


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def write(self, vals):
        if {'product_qty', 'price_unit', 'product_id'} & set(vals) and not self.env.context.get('mos_amendment_apply'):
            for line in self:
                if line.order_id.state in ('purchase', 'done'):
                    raise UserError(self.env._('%s is confirmed: change it through a purchase order amendment.', line.order_id.display_name))
        return super().write(vals)


class MosPoAmendment(models.Model):
    """A change to a confirmed purchase order, justified and, above the matrix threshold, approved (FR-PRC-018, 019)."""

    _name = 'mos.po.amendment'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS purchase order amendment'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state', 'applied_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    purchase_id = fields.Many2one('purchase.order', required=True, ondelete='restrict', domain="[('state', 'in', ('purchase', 'done'))]")
    reason = fields.Char(required=True)
    line_ids = fields.One2many('mos.po.amendment.line', 'amendment_id', string='Changes')
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    applied_by_id = fields.Many2one('res.users', readonly=True)
    new_total = fields.Float(compute='_compute_new_total', string='Order value after the change')
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('applied', 'Applied'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.depends('purchase_id', 'line_ids.new_qty', 'line_ids.new_price')
    def _compute_new_total(self):
        for amd in self:
            changed = {line.po_line_id: line for line in amd.line_ids}
            total = 0.0
            for pol in amd.purchase_id.order_line:
                line = changed.get(pol)
                total += (line.new_qty if line else pol.product_qty) * (line.new_price if line else pol.price_unit)
            amd.new_total = total

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.po.amendment')
        return super().create(vals_list)

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.new_total

    def _mos_approval_site(self):
        return self.purchase_id._mos_site_of()

    def _mos_approval_done(self, request):
        self.sudo().write({'mos_approval_state': 'approved', 'state': 'approved'})

    def _mos_approval_closed(self, request, state):
        if state != 'approved':
            self.sudo().write({'mos_approval_state': state, 'state': 'draft' if state in ('returned', 'cancelled') else 'rejected'})

    def action_submit(self):
        dialog = self._mos_attestation_dialog('action_submit')
        if dialog:
            return dialog
        for amd in self:
            if amd.state != 'draft':
                raise UserError(self.env._('Only a draft amendment can be submitted.'))
            if not amd.line_ids:
                raise UserError(self.env._('List at least one change.'))
            if amd._mos_approval_required():
                amd.action_mos_submit_approval()
                amd.write({'state': 'pending'})
            else:
                amd.write({'state': 'approved', 'mos_approval_state': 'approved'})

    def action_apply(self):
        for amd in self:
            if amd.state != 'approved':
                raise UserError(self.env._('Only an approved amendment can be applied.'))
            if amd.requested_by_id == self.env.user and not amd.mos_approval_request_id:
                raise UserError(self.env._('An amendment that needed no approval must be applied by someone other than its requester.'))
            for line in amd.line_ids:
                if line.new_qty < line.po_line_id.qty_received:
                    raise UserError(self.env._('%(item)s: %(got)s already received, the new quantity cannot be lower.', item=line.po_line_id.product_id.display_name, got=line.po_line_id.qty_received))
                line.po_line_id.sudo().with_context(mos_amendment_apply=True).write({'product_qty': line.new_qty, 'price_unit': line.new_price})
            amd.write({'state': 'applied', 'applied_by_id': self.env.uid})
            amd.purchase_id.message_post(body=self.env._('Amended (%(name)s): %(reason)s', name=amd.name, reason=amd.reason))
            self.env['mos.audit.event'].record('po_amended', amd, f'{amd.name} on {amd.purchase_id.name}')

    def action_cancel(self):
        for amd in self:
            if amd.state in ('applied', 'rejected', 'cancelled'):
                raise UserError(self.env._('This amendment is closed.'))
            if amd.state == 'pending':
                amd.action_mos_recall_approval()
            amd.write({'state': 'cancelled'})


class MosPoAmendmentLine(models.Model):
    _name = 'mos.po.amendment.line'
    _description = 'MOS purchase order amendment line'

    amendment_id = fields.Many2one('mos.po.amendment', required=True, ondelete='cascade')
    po_line_id = fields.Many2one('purchase.order.line', required=True, domain="[('order_id', '=', parent.purchase_id)]")
    old_qty = fields.Float(readonly=True, digits=(14, 1))
    old_price = fields.Float(readonly=True, digits=(12, 4))
    new_qty = fields.Float(required=True, digits=(14, 1))
    new_price = fields.Float(required=True, digits=(12, 4))

    @api.constrains('new_qty', 'new_price')
    def _check(self):
        for line in self:
            if line.new_qty <= 0 or line.new_price < 0:
                raise ValidationError(self.env._('The new quantity must be above zero and the price cannot be negative.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pol = self.env['purchase.order.line'].browse(vals['po_line_id'])
            vals['old_qty'], vals['old_price'] = pol.product_qty, pol.price_unit
        return super().create(vals_list)


class MosSupplierReturn(models.Model):
    """Goods sent back to the supplier. Dispatch moves the stock out; the credit becomes a debit note against the
    invoice, so the payable falls by the value returned (FR-PRC-025..027)."""

    _name = 'mos.supplier.return'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS supplier return'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    purchase_id = fields.Many2one('purchase.order', required=True, ondelete='restrict', domain="[('state', 'in', ('purchase', 'done'))]")
    supplier_id = fields.Many2one(related='purchase_id.partner_id', store=True)
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(required=True, digits=(14, 1))
    location_id = fields.Many2one('stock.location', string='Take from', domain="[('usage', '=', 'internal')]")
    reason_id = fields.Many2one('mos.reason', required=True, domain=[('domain', 'in', ('quality', 'adjustment'))])
    note = fields.Char()
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    debit_note_id = fields.Many2one('mos.debit.note', readonly=True, copy=False)
    state = fields.Selection(
        [('draft', 'Draft'), ('approved', 'Approved'), ('dispatched', 'Dispatched'), ('credited', 'Credited'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.supplier.return')
        return super().create(vals_list)

    @api.constrains('qty', 'purchase_id', 'product_id')
    def _check_qty(self):
        for ret in self:
            if ret.qty <= 0:
                raise ValidationError(self.env._('Return a quantity above zero.'))
            lines = ret.purchase_id.order_line.filtered(lambda line: line.product_id == ret.product_id)
            if not lines:
                raise ValidationError(self.env._('%s is not on that purchase order.', ret.product_id.display_name))
            already = sum(self.search([('purchase_id', '=', ret.purchase_id.id), ('product_id', '=', ret.product_id.id), ('state', '!=', 'cancelled'), ('id', '!=', ret.id)]).mapped('qty'))
            if ret.qty + already > sum(lines.mapped('qty_received')) + 0.0001:
                raise ValidationError(self.env._('Only %s kg were received on that order, so no more can go back.', sum(lines.mapped('qty_received'))))

    def _mos_unit_price(self):
        self.ensure_one()
        lines = self.purchase_id.order_line.filtered(lambda line: line.product_id == self.product_id)
        return lines[:1].price_unit

    def action_approve(self):
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can approve a return.'))
        for ret in self:
            if ret.state != 'draft':
                raise UserError(self.env._('Only a draft return can be approved.'))
            if ret.requested_by_id == user:
                raise UserError(self.env._('You cannot approve your own return.'))
            ret.write({'state': 'approved', 'approved_by_id': user.id})

    def action_dispatch(self):
        for ret in self:
            if ret.state != 'approved':
                raise UserError(self.env._('Only an approved return can be dispatched.'))
            order = ret.purchase_id.sudo()
            warehouse = order.picking_type_id.warehouse_id
            source = ret.location_id or warehouse.lot_stock_id
            picking = self.env['stock.picking'].sudo().create(
                {
                    'picking_type_id': warehouse.out_type_id.id,
                    'location_id': source.id,
                    'location_dest_id': self.env.ref('stock.stock_location_suppliers').id,
                    'partner_id': ret.supplier_id.id,
                    'origin': ret.name,
                    'move_ids': [
                        Command.create(
                            {
                                'product_id': ret.product_id.id,
                                'product_uom_qty': ret.qty,
                                'location_id': source.id,
                                'location_dest_id': self.env.ref('stock.stock_location_suppliers').id,
                            }
                        )
                    ],
                }
            )
            picking.action_confirm()
            picking.move_ids.write({'quantity': ret.qty, 'picked': True})
            result = picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._('The outgoing transfer needs attention in Inventory.'))
            ret.write({'state': 'dispatched', 'picking_id': picking.id})
            self.env['mos.audit.event'].record('supplier_return_dispatched', ret, f'{ret.name}: {ret.qty} kg')

    def action_credit(self, invoice):
        """Raise the debit note for the returned goods against ``invoice``."""
        for ret in self:
            if ret.state != 'dispatched':
                raise UserError(self.env._('Dispatch the goods before asking the supplier for credit.'))
            if invoice.purchase_id != ret.purchase_id:
                raise UserError(self.env._('The invoice belongs to a different purchase order.'))
            amount = round(ret.qty * ret._mos_unit_price() * (1 + invoice.vat_pct / 100.0), 2)
            if amount > invoice.payable_amount + 0.005:
                raise UserError(self.env._('The credit (%(credit)s) is more than the invoice still has payable (%(payable)s).', credit=amount, payable=invoice.payable_amount))
            note = self.env['mos.debit.note'].sudo().create(
                {'invoice_id': invoice.id, 'supplier_id': ret.supplier_id.id, 'amount': amount, 'reason': self.env._('Returned goods %(name)s: %(reason)s', name=ret.name, reason=ret.reason_id.name)}
            )
            ret.write({'state': 'credited', 'debit_note_id': note.id})

    def action_cancel(self):
        for ret in self:
            if ret.state in ('dispatched', 'credited', 'cancelled'):
                raise UserError(self.env._('The goods have left; this return cannot be cancelled.'))
            ret.state = 'cancelled'


class MosGrnReassignment(models.Model):
    """A delivery booked against the wrong purchase order is moved to the right one before its receipt is confirmed (FR-PRC-028)."""

    _name = 'mos.grn.reassignment'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS goods receipt reassignment'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected')

    name = fields.Char(readonly=True, copy=False, default='New')
    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='restrict', domain="[('purpose', '=', 'inbound_po')]")
    from_order_id = fields.Many2one('purchase.order', readonly=True)
    to_order_id = fields.Many2one('purchase.order', required=True, domain="[('state', 'in', ('purchase', 'done'))]")
    reason = fields.Char(required=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection([('requested', 'Requested'), ('applied', 'Applied'), ('rejected', 'Rejected')], default='requested', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.grn.reassignment')
            visit = self.env['mos.gate.visit'].browse(vals['visit_id'])
            vals['from_order_id'] = visit.sudo().purchase_id.id
        return super().create(vals_list)

    def action_apply(self):
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can reassign a receipt.'))
        for re_ in self:
            if re_.state != 'requested':
                raise UserError(self.env._('This reassignment is already closed.'))
            if re_.requested_by_id == user:
                raise UserError(self.env._('Someone other than the requester must approve the reassignment.'))
            visit = re_.visit_id.sudo()
            if visit.grn_state in ('confirmed', 'rejected'):
                raise UserError(self.env._('The receipt is already %s: it cannot be moved.', visit.grn_state))
            target = re_.to_order_id.sudo()
            if target.partner_id != visit.purchase_id.partner_id:
                raise UserError(self.env._('Both orders must be with the same supplier.'))
            if target == visit.purchase_id:
                raise UserError(self.env._('The delivery is already on that order.'))
            if visit.grn_picking_id:
                moves = visit.grn_picking_id.move_ids
                moves.write({'quantity': 0, 'picked': False})
                moves.write({'location_dest_id': visit.grn_picking_id.picking_type_id.default_location_dest_id.id})
                visit.grn_picking_id.write({'location_dest_id': visit.grn_picking_id.picking_type_id.default_location_dest_id.id})
            visit.write({'purchase_id': target.id, 'grn_picking_id': False, 'grn_state': 'none'})
            re_.write({'state': 'applied', 'approved_by_id': user.id})
            self.env['mos.audit.event'].record('grn_reassigned', visit, f'{visit.name}: {re_.from_order_id.name} -> {target.name}')

    def action_reject(self):
        for re_ in self:
            if re_.state != 'requested':
                raise UserError(self.env._('This reassignment is already closed.'))
            re_.state = 'rejected'
