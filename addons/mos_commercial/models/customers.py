from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


def _has(user, *groups):
    return any(user.has_group(g) for g in groups)


class ResPartner(models.Model):
    """A customer is approved before the first order, and its credit limit changes only through an approved request (FR-SAL-016)."""

    _inherit = 'res.partner'

    mos_customer_state = fields.Selection(
        [('draft', 'Not approved'), ('approved', 'Approved'), ('blocked', 'Blocked')],
        string='Customer standing',
        default='draft',
        copy=False,
        tracking=True,
    )
    mos_customer_approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    mos_credit_change_ids = fields.One2many('mos.credit.change', 'partner_id', string='Credit limit changes')

    def action_approve_customer(self):
        user = self.env.user
        if not _has(user, 'mos_approvals.group_mos_finance_manager', 'mos_commercial.group_mos_sales_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a sales or finance manager can approve a customer.'))
        for partner in self:
            if partner.create_uid == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The person who registered a customer cannot approve it.'))
            partner.sudo().write({'mos_customer_state': 'approved', 'mos_customer_approved_by_id': user.id})
            self.env['mos.audit.event'].record('customer_approved', partner, partner.display_name)

    def action_block_customer(self, reason=''):
        if not _has(self.env.user, 'mos_approvals.group_mos_finance_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a finance manager can block a customer.'))
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        for partner in self:
            partner.sudo().write({'mos_customer_state': 'blocked'})
            self.env['mos.audit.event'].record('customer_blocked', partner, f'{partner.display_name}: {reason}')

    def write(self, vals):
        if 'mos_credit_limit' in vals and not self.env.context.get('mos_credit_change'):
            if self.env['ir.config_parameter'].sudo().get_bool('mos_commercial.control_credit_changes'):
                raise UserError(self.env._('A credit limit is changed through a credit limit change request, which a finance manager approves.'))
        return super().write(vals)


class MosCreditChange(models.Model):
    """A request to raise or lower a customer's credit limit. The requester cannot approve it."""

    _name = 'mos.credit.change'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS credit limit change'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected')

    name = fields.Char(readonly=True, copy=False, default='New')
    partner_id = fields.Many2one('res.partner', required=True, ondelete='restrict')
    old_limit = fields.Float(readonly=True)
    new_limit = fields.Float(required=True)
    reason = fields.Char(required=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection([('requested', 'Requested'), ('applied', 'Applied'), ('rejected', 'Rejected')], default='requested', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            partner = self.env['res.partner'].browse(vals['partner_id'])
            vals['old_limit'] = partner.mos_credit_limit
            if vals.get('new_limit', 0) < 0:
                raise ValidationError(self.env._('A credit limit cannot be negative.'))
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.credit.change')
        return super().create(vals_list)

    def action_approve(self):
        user = self.env.user
        if not _has(user, 'mos_approvals.group_mos_finance_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a finance manager can approve a credit limit change.'))
        for change in self:
            if change.state != 'requested':
                raise UserError(self.env._('This request is closed.'))
            if change.requested_by_id == user:
                raise UserError(self.env._('You cannot approve your own request.'))
            change.partner_id.sudo().with_context(mos_credit_change=True).write({'mos_credit_limit': change.new_limit})
            change.write({'state': 'applied', 'approved_by_id': user.id})
            self.env['mos.audit.event'].record('credit_limit_changed', change, f'{change.partner_id.display_name}: {change.old_limit} -> {change.new_limit}: {change.reason}')

    def action_reject(self):
        for change in self:
            if change.state != 'requested':
                raise UserError(self.env._('This request is closed.'))
            change.state = 'rejected'


class SaleOrder(models.Model):
    """Confirmation also needs an approved customer, approved prices and a price list that has been approved (FR-SAL-002, 016, 017)."""

    _inherit = 'sale.order'

    mos_price_exception = fields.Boolean(compute='_compute_price_exception', string='Price outside the rules')
    mos_price_approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    @api.depends('order_line.mos_price_exception')
    def _compute_price_exception(self):
        for order in self:
            order.mos_price_exception = any(order.order_line.mapped('mos_price_exception'))

    def action_approve_prices(self, note):
        """A sales manager accepts prices below the floor or discounts above the limit (SAL-002)."""
        user = self.env.user
        if not _has(user, 'mos_commercial.group_mos_sales_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a sales manager can approve prices outside the rules.'))
        if not note:
            raise UserError(self.env._('Say why the prices are acceptable.'))
        for order in self:
            if order.create_uid == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('You cannot approve the prices of your own order.'))
            order.sudo().write({'mos_price_approved_by_id': user.id})
            self.env['mos.audit.event'].record('prices_approved', order, f'{order.name}: {note}')

    def action_confirm(self):
        icp = self.env['ir.config_parameter'].sudo()
        for order in self.filtered(lambda o: o.state in ('draft', 'sent')):
            partner = order.partner_id.commercial_partner_id
            if icp.get_bool('mos_commercial.enforce_customer_approval'):
                if partner.mos_customer_state == 'blocked':
                    raise UserError(self.env._('%s is blocked.', partner.display_name))
                if partner.mos_customer_state != 'approved':
                    raise UserError(self.env._('%s has not been approved as a customer yet.', partner.display_name))
            if icp.get_bool('mos_commercial.enforce_pricing'):
                if order.mos_price_exception and not order.mos_price_approved_by_id:
                    raise UserError(self.env._('Some prices are below the floor or the discount is above the limit: a sales manager must approve them.'))
                if order.pricelist_id and getattr(order.pricelist_id, 'mos_state', 'approved') != 'approved':
                    raise UserError(self.env._('The price list %s has not been approved.', order.pricelist_id.display_name))
        return super().action_confirm()


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    mos_price_exception = fields.Boolean(compute='_compute_mos_price_exception', store=True)

    @api.depends('price_unit', 'discount', 'product_id')
    def _compute_mos_price_exception(self):
        icp = self.env['ir.config_parameter'].sudo()
        rep_limit = icp.get_float('mos_commercial.max_discount_rep', 3.0)
        for line in self:
            floor = self.env['mos.price.floor'].sudo().search([('product_id', '=', line.product_id.id)], limit=1)
            net = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            line.mos_price_exception = bool(line.product_id) and bool((floor and net < floor.floor_price - 0.00001) or (line.discount or 0.0) > rep_limit + 0.00001)


class MosPriceFloor(models.Model):
    """The lowest net price a product may be sold at without a sales manager (FR-SAL-002)."""

    _name = 'mos.price.floor'
    _inherit = ['mos.audited']
    _description = 'MOS price floor'
    _rec_name = 'product_id'

    product_id = fields.Many2one('product.product', required=True, ondelete='cascade')
    floor_price = fields.Float(required=True, digits=(12, 4))

    _product_uniq = models.Constraint('unique(product_id)', 'This product already has a price floor.')

    @api.constrains('floor_price')
    def _check(self):
        for floor in self:
            if floor.floor_price < 0:
                raise ValidationError(self.env._('A floor price cannot be negative.'))


class ProductPricelist(models.Model):
    """A price list takes effect only after a sales manager other than its author approves it (FR-SAL-017)."""

    _inherit = 'product.pricelist'

    mos_state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved')], default='draft', copy=False, string='Approval')
    mos_approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)

    def action_mos_approve(self):
        user = self.env.user
        if not _has(user, 'mos_commercial.group_mos_sales_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a sales manager can approve a price list.'))
        for pricelist in self:
            if pricelist.create_uid == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The author of a price list cannot approve it.'))
            pricelist.sudo().write({'mos_state': 'approved', 'mos_approved_by_id': user.id})
            self.env['mos.audit.event'].record('pricelist_approved', pricelist, pricelist.display_name)

    def write(self, vals):
        guarded = {'item_ids', 'currency_id'}
        if guarded & set(vals) and self.env['ir.config_parameter'].sudo().get_bool('mos_commercial.enforce_pricing'):
            for pricelist in self.filtered(lambda p: p.mos_state == 'approved'):
                vals = dict(vals, mos_state='draft', mos_approved_by_id=False)
        return super().write(vals)
