from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosPurchaseContract(models.Model):
    """A standing agreement with a supplier for a quantity of an item at an agreed price over a period; orders placed against it
    are call-offs and can never add up to more than the contract holds (FR-PRC-018)."""

    _name = 'mos.purchase.contract'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS purchase contract'
    _order = 'id desc'
    _mos_final_states = ('active', 'closed', 'cancelled')
    _mos_editable_after_post = {'state', 'approved_by_id', 'closed_note'}

    name = fields.Char(readonly=True, copy=False, default='New')
    supplier_id = fields.Many2one('res.partner', required=True, domain="[('supplier_rank', '>', 0)]")
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(string='Contract quantity', required=True, digits=(14, 1))
    price = fields.Float(string='Agreed price', required=True, digits=(12, 4))
    valid_from = fields.Date(required=True, default=fields.Date.context_today)
    valid_to = fields.Date(required=True)
    quality_note = fields.Text(string='Quality terms', help='Quality rules agreed with the supplier for deliveries under this contract.')
    spec_id = fields.Many2one('mos.lab.spec', string='Specification', domain="[('product_id', '=', product_id)]")
    state = fields.Selection([('draft', 'Draft'), ('active', 'Active'), ('closed', 'Closed'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    closed_note = fields.Char(readonly=True)
    order_ids = fields.One2many('purchase.order', 'mos_contract_id', string='Call-offs')
    called_off = fields.Float(compute='_compute_called_off', digits=(14, 1), string='Called off')
    remaining = fields.Float(compute='_compute_called_off', digits=(14, 1), string='Remaining')

    @api.depends('qty', 'order_ids.state', 'order_ids.order_line.product_qty', 'order_ids.order_line.product_id')
    def _compute_called_off(self):
        for contract in self:
            used = contract._called_off_qty()
            contract.called_off = used
            contract.remaining = round(contract.qty - used, 1)

    def _called_off_qty(self, exclude=None):
        self.ensure_one()
        orders = self.order_ids.filtered(lambda o: o.state != 'cancel' and o not in (exclude or self.env['purchase.order']))
        return sum(line.product_qty for o in orders for line in o.order_line if line.product_id == self.product_id)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.purchase.contract')
            if vals.get('qty', 1) <= 0 or vals.get('price', 0) < 0:
                raise ValidationError(self.env._('The quantity must be above zero and the price cannot be negative.'))
            if vals.get('valid_to') and vals.get('valid_from') and fields.Date.to_date(vals['valid_to']) < fields.Date.to_date(vals['valid_from']):
                raise ValidationError(self.env._('A contract cannot end before it starts.'))
        return super().create(vals_list)

    def action_activate(self):
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can activate a contract.'))
        for contract in self:
            if contract.state != 'draft':
                raise UserError(self.env._('Only a draft contract can be activated.'))
            if contract.create_uid == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The person who drew up a contract cannot activate it.'))
            contract.write({'state': 'active', 'approved_by_id': user.id})
            self.env['mos.audit.event'].record('contract_activated', contract, f'{contract.name}: {contract.supplier_id.display_name} {contract.qty} {contract.product_id.display_name} at {contract.price}')

    def action_close(self, note=''):
        for contract in self:
            if contract.state != 'active':
                raise UserError(self.env._('Only an active contract can be closed.'))
            contract.write({'state': 'closed', 'closed_note': note})

    def action_cancel(self):
        for contract in self:
            if contract.state not in ('draft', 'active') or contract._called_off_qty():
                raise UserError(self.env._('A contract with call-offs cannot be cancelled; close it instead.'))
            contract.write({'state': 'cancelled'})

    def action_new_calloff(self, qty):
        """A draft order for `qty` against this contract, at the agreed price."""
        self.ensure_one()
        order = self.env['purchase.order'].create(
            {
                'partner_id': self.supplier_id.id,
                'mos_contract_id': self.id,
                'order_line': [(0, 0, {'product_id': self.product_id.id, 'product_qty': qty, 'price_unit': self.price})],
            }
        )
        return order


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    mos_contract_id = fields.Many2one('mos.purchase.contract', string='Contract', copy=False, domain="[('state', '=', 'active'), ('supplier_id', '=', partner_id)]")

    def _mos_check_contract(self):
        for order in self.filtered('mos_contract_id'):
            contract = order.mos_contract_id.sudo()
            if contract.state != 'active':
                raise UserError(self.env._('Contract %s is not active.', contract.name))
            if contract.supplier_id != order.partner_id:
                raise UserError(self.env._('Contract %(c)s is with %(s)s, not %(p)s.', c=contract.name, s=contract.supplier_id.display_name, p=order.partner_id.display_name))
            today = fields.Date.context_today(self)
            if not contract.valid_from <= today <= contract.valid_to:
                raise UserError(self.env._('Contract %(c)s is valid from %(a)s to %(b)s.', c=contract.name, a=contract.valid_from, b=contract.valid_to))
            lines = order.order_line.filtered(lambda l: l.product_id == contract.product_id)
            if not lines:
                raise UserError(self.env._('Contract %(c)s covers %(p)s, which this order does not contain.', c=contract.name, p=contract.product_id.display_name))
            if any(abs(l.price_unit - contract.price) > 0.00005 for l in lines):
                raise UserError(self.env._('The contract price is %(price)s; the order has a different price for %(p)s.', price=contract.price, p=contract.product_id.display_name))
            used = contract._called_off_qty(exclude=order) + sum(lines.mapped('product_qty'))
            if used > contract.qty + 0.0001:
                raise UserError(self.env._('This call-off would take %(used)s against a contract of %(qty)s: only %(left)s is left.', used=used, qty=contract.qty, left=round(contract.qty - contract._called_off_qty(exclude=order), 1)))

    def button_confirm(self):
        self._mos_check_contract()
        return super().button_confirm()

    def _mos_check_quantity(self):
        for order in self.filtered('mos_contract_id'):
            contract = order.mos_contract_id.sudo()
            lines = order.order_line.filtered(lambda l: l.product_id == contract.product_id)
            if order.state == 'cancel' or not lines:
                continue
            others = contract._called_off_qty(exclude=order)
            if others + sum(lines.mapped('product_qty')) > contract.qty + 0.0001:
                raise ValidationError(self.env._('A call-off cannot take more than the contract has left: %(left)s of %(qty)s.', left=round(contract.qty - others, 1), qty=contract.qty))

    @api.constrains('mos_contract_id', 'order_line')
    def _check_contract_quantity(self):
        self._mos_check_quantity()


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.constrains('product_qty', 'product_id')
    def _check_contract_quantity(self):
        self.order_id._mos_check_quantity()
