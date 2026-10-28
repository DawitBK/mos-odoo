from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosPurchaseRequest(models.Model):
    """The header of a purchase requisition as the client's screen draws it (FR-PRC-007)."""

    _inherit = 'mos.purchase.request'

    request_date = fields.Date(required=True, default=fields.Date.context_today)
    priority = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent')], default='medium', required=True)
    requester_ref = fields.Char(string='Requester employee ID', default=lambda self: self.env.user.mos_employee_ref)
    remarks = fields.Text()
    line_count = fields.Integer(compute='_compute_line_count')

    @api.depends('line_ids')
    def _compute_line_count(self):
        for req in self:
            req.line_count = len(req.line_ids)

    @api.constrains('request_date', 'needed_by')
    def _check_dates(self):
        for req in self:
            if req.needed_by and req.request_date and req.needed_by < req.request_date:
                raise ValidationError(self.env._('The required date cannot be before the request date.'))

    def action_convert(self, supplier):
        """The order asks for the quantity to purchase on each line (what is needed less what is in stock), and leaves out items the
        stock already covers."""
        self.ensure_one()
        if self.state == 'approved' and not any(line.qty_to_purchase > 0 for line in self.line_ids):
            raise UserError(self.env._('Stock already covers every item on this request: there is nothing to order.'))
        order = super().action_convert(supplier)
        order.mos_department = self.department
        wanted = {}
        for line in self.line_ids:
            wanted.setdefault(line.product_id.id, []).append(line)
        for order_line in order.order_line:
            lines = wanted.get(order_line.product_id.id)
            if lines:
                source = lines.pop(0)
                if source.qty_to_purchase > 0:
                    order_line.product_qty = source.qty_to_purchase
                else:
                    order_line.unlink()
        return order


class MosPurchaseRequestLine(models.Model):
    _inherit = 'mos.purchase.request.line'

    category_id = fields.Many2one(related='product_id.categ_id', string='Category')
    available_qty = fields.Float(string='Available stock', compute='_compute_available', digits=(14, 3), help='Free to use now at the site of the request.')
    proposed_qty = fields.Float(string='Proposed to purchase', compute='_compute_available', digits=(14, 3))
    qty_to_purchase = fields.Float(
        string='Quantity to purchase', compute='_compute_qty_to_purchase', store=True, readonly=False, digits=(14, 3), help='Needed less what is available; it can be raised, with a reason, never lowered.'
    )
    increase_reason = fields.Char(string='Reason for ordering more')
    line_total = fields.Float(string='Estimated total', compute='_compute_line_total', digits=(14, 2))
    specification = fields.Char(string='Specification (link)')
    note = fields.Char()

    def _site_stock(self, product):
        """What is free to use of the product at the request's site: available-status locations of its warehouse, less what is reserved."""
        self.ensure_one()
        warehouse = self.request_id.site_id.warehouse_id
        if not (product and warehouse):
            return 0.0
        quants = self.env['stock.quant'].sudo().search(
            [
                ('product_id', '=', product.id),
                ('location_id', 'child_of', warehouse.view_location_id.id),
                ('location_id.usage', '=', 'internal'),
                ('location_id.mos_stock_status', '=', 'available'),
            ]
        )
        free = sum(quants.mapped('quantity')) - sum(quants.mapped('reserved_quantity'))
        if self.env['ir.config_parameter'].sudo().get_bool('mos_request_forms.subtract_open_orders'):
            open_lines = self.env['purchase.order.line'].sudo().search([('product_id', '=', product.id), ('order_id.state', '=', 'purchase')])
            free += sum(max(line.product_qty - line.qty_received, 0.0) for line in open_lines)
        return max(free, 0.0)

    @api.depends('product_id', 'qty', 'request_id.site_id')
    def _compute_available(self):
        for line in self:
            line.available_qty = line._site_stock(line.product_id)
            line.proposed_qty = max(line.qty - line.available_qty, 0.0)

    @api.depends('product_id', 'qty', 'request_id.site_id')
    def _compute_qty_to_purchase(self):
        for line in self:
            line.qty_to_purchase = max(line.qty - line._site_stock(line.product_id), 0.0)

    @api.depends('qty', 'estimated_price')
    def _compute_line_total(self):
        for line in self:
            line.line_total = line.qty * line.estimated_price

    @api.constrains('qty_to_purchase', 'qty', 'product_id')
    def _check_qty_to_purchase(self):
        for line in self:
            proposed = max(line.qty - line._site_stock(line.product_id), 0.0)
            if line.qty_to_purchase < proposed - 0.0005:
                raise ValidationError(self.env._('%(item)s: the quantity to purchase cannot be below %(proposed)s (needed less what is available).', item=line.product_id.display_name, proposed=proposed))
            if line.qty_to_purchase > proposed + 0.0005 and not (line.increase_reason or '').strip():
                raise ValidationError(self.env._('%s: say why more than the proposed quantity is ordered.', line.product_id.display_name))


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    mos_department = fields.Char(string='Purchasing department')
