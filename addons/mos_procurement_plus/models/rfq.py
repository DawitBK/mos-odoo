from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosRfq(models.Model):
    """A request for quotation: the suppliers invited, what each offered and the comparison, ending in an award that becomes the
    purchase order. The sourcing policy's minimum number of quotes is enforced at the award (FR-PRC-008, 009)."""

    _name = 'mos.rfq'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS request for quotation'
    _order = 'id desc'
    _mos_final_states = ('awarded', 'cancelled')
    _mos_editable_after_post = {'state', 'order_id', 'awarded_quote_id', 'award_reason', 'awarded_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user.mos_site_ids[:1])
    request_id = fields.Many2one('mos.purchase.request', domain="[('state', '=', 'approved')]")
    deadline = fields.Date()
    line_ids = fields.One2many('mos.rfq.line', 'rfq_id', string='What we need')
    quote_ids = fields.One2many('mos.rfq.quote', 'rfq_id', string='Offers')
    invited_ids = fields.Many2many('res.partner', string='Invited suppliers', domain="[('supplier_rank', '>', 0)]")
    awarded_quote_id = fields.Many2one('mos.rfq.quote', readonly=True)
    award_reason = fields.Char(readonly=True)
    awarded_by_id = fields.Many2one('res.users', readonly=True)
    order_id = fields.Many2one('purchase.order', readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('sent', 'Out for quotes'), ('awarded', 'Awarded'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.rfq')
        return super().create(vals_list)

    @api.onchange('request_id')
    def _onchange_request(self):
        if self.request_id and not self.line_ids:
            self.line_ids = [Command.create({'product_id': l.product_id.id, 'qty': l.qty}) for l in self.request_id.line_ids]

    def action_send(self):
        for rfq in self:
            if rfq.state != 'draft':
                raise UserError(self.env._('Only a draft request can be sent.'))
            if not rfq.line_ids or not rfq.invited_ids:
                raise UserError(self.env._('List what is needed and invite at least one supplier.'))
            blocked = rfq.invited_ids.filtered(lambda p: p.mos_supplier_state in ('blocked', 'suspended'))
            if blocked:
                raise UserError(self.env._('These suppliers cannot be invited: %s.', ', '.join(blocked.mapped('display_name'))))
            rfq.state = 'sent'

    def action_award(self, quote, reason=''):
        """Award the order to one offer. Anything but the lowest total needs a reason; too few offers needs the policy waived."""
        self.ensure_one()
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_user') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a buyer can award a request for quotation.'))
        if self.state != 'sent':
            raise UserError(self.env._('Send the request and collect offers before awarding it.'))
        if quote.rfq_id != self or quote.total <= 0:
            raise UserError(self.env._('Choose one of the offers on this request.'))
        offers = self.quote_ids.filtered(lambda q: q.total > 0 and q.complete)
        policy = self.env['mos.sourcing.policy'].quotes_needed(quote.total, self.site_id)
        if len(offers) < policy and not reason:
            raise UserError(self.env._('The sourcing policy asks for %(need)s complete offers and there are %(have)s: give the reason for awarding anyway.', need=policy, have=len(offers)))
        lowest = min(offers.mapped('total')) if offers else quote.total
        if quote.total > lowest + 0.005 and not reason:
            raise UserError(self.env._('This is not the lowest offer (%(low)s): give the reason.', low=lowest))
        if not quote.complete:
            raise UserError(self.env._('That offer does not price every line.'))
        supplier = quote.partner_id
        if self.env['ir.config_parameter'].sudo().get_bool('mos_procurement.enforce_asl') and supplier.mos_supplier_state != 'approved':
            raise UserError(self.env._('%s is not on the approved supplier list.', supplier.display_name))
        order = self.env['purchase.order'].create(
            {
                'partner_id': supplier.id,
                'origin': self.name,
                'mos_request_id': self.request_id.id,
                'mos_sole_source_reason': reason if len(offers) < policy else False,
                'order_line': [Command.create({'product_id': l.product_id.id, 'product_qty': l.qty, 'price_unit': ql.unit_price, 'name': l.product_id.display_name}) for l, ql in ((line, quote.line_ids.filtered(lambda x: x.rfq_line_id == line)[:1]) for line in self.line_ids)],
                'mos_quote_ids': [Command.create({'partner_id': q.partner_id.id, 'amount': q.total, 'valid_until': q.valid_until, 'note': q.note}) for q in offers],
            }
        )
        self.write({'state': 'awarded', 'order_id': order.id, 'awarded_quote_id': quote.id, 'award_reason': reason, 'awarded_by_id': user.id})
        self.env['mos.audit.event'].record('rfq_awarded', self, f'{self.name}: {supplier.display_name}, {quote.total}, order {order.name} {reason}')
        return order

    def action_cancel(self):
        for rfq in self:
            if rfq.state in ('awarded', 'cancelled'):
                raise UserError(self.env._('This request is closed.'))
            rfq.state = 'cancelled'


class MosRfqLine(models.Model):
    _name = 'mos.rfq.line'
    _description = 'MOS RFQ line'

    rfq_id = fields.Many2one('mos.rfq', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('purchase_ok', '=', True)]")
    qty = fields.Float(required=True, digits=(14, 1))

    @api.constrains('qty')
    def _check(self):
        for line in self:
            if line.qty <= 0:
                raise ValidationError(self.env._('The quantity must be above zero.'))


class MosRfqQuote(models.Model):
    """One supplier's offer for the request: a price per line, how soon, and for how long it holds."""

    _name = 'mos.rfq.quote'
    _inherit = ['mos.audited']
    _description = 'MOS RFQ offer'
    _order = 'total'

    rfq_id = fields.Many2one('mos.rfq', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', required=True, domain="[('supplier_rank', '>', 0)]")
    lead_days = fields.Integer()
    valid_until = fields.Date()
    note = fields.Char()
    line_ids = fields.One2many('mos.rfq.quote.line', 'quote_id')
    total = fields.Float(compute='_compute_total', store=True)
    complete = fields.Boolean(compute='_compute_total', store=True)

    _supplier_once = models.Constraint('unique(rfq_id, partner_id)', 'A supplier gives one offer per request.')

    @api.depends('line_ids.unit_price', 'line_ids.rfq_line_id', 'rfq_id.line_ids')
    def _compute_total(self):
        for quote in self:
            total, priced = 0.0, 0
            for line in quote.line_ids:
                total += line.unit_price * line.rfq_line_id.qty
                priced += 1 if line.unit_price > 0 else 0
            quote.total = round(total, 2)
            quote.complete = priced == len(quote.rfq_id.line_ids) and priced > 0

    @api.constrains('partner_id', 'rfq_id')
    def _check_invited(self):
        for quote in self:
            if quote.rfq_id.invited_ids and quote.partner_id not in quote.rfq_id.invited_ids:
                raise ValidationError(self.env._('%s was not invited.', quote.partner_id.display_name))


class MosRfqQuoteLine(models.Model):
    _name = 'mos.rfq.quote.line'
    _description = 'MOS RFQ offer line'

    quote_id = fields.Many2one('mos.rfq.quote', required=True, ondelete='cascade')
    rfq_line_id = fields.Many2one('mos.rfq.line', required=True, domain="[('rfq_id', '=', parent.rfq_id)]")
    unit_price = fields.Float(digits=(12, 4))

    @api.constrains('unit_price')
    def _check(self):
        for line in self:
            if line.unit_price < 0:
                raise ValidationError(self.env._('A price cannot be negative.'))
