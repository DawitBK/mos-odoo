from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.mos_core.units import totals_html


class MosStockIssue(models.Model):
    """Goods leave the store without a sale or a production run: consumed by a department, or scrapped (FR-INV-006).

    Valued at the current average cost by the stock move itself. Only stock in an available location can be issued, and the matrix
    decides whether an approval is needed (scrap normally has one).
    """

    _name = 'mos.stock.issue'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS goods issue'
    _order = 'id desc'
    _mos_final_states = ('issued', 'rejected', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state', 'issued_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    kind = fields.Selection([('consumption', 'Consumption by a department'), ('scrap', 'Scrap')], default='consumption', required=True, tracking=True)
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user._mos_current_site())
    location_id = fields.Many2one('stock.location', string='Issue from', required=True, domain="[('usage', '=', 'internal'), ('mos_stock_status', '=', 'available')]")
    department_id = fields.Many2one('mos.department', string='Issued to (department)', default=lambda self: self.env.user.mos_department_id)
    reason_id = fields.Many2one('mos.reason', domain=[('domain', '=', 'scrap')])
    note = fields.Char(string='Comments')
    date = fields.Date(default=fields.Date.context_today, required=True)
    line_ids = fields.One2many('mos.stock.issue.line', 'issue_id', string='Products')
    amount = fields.Float(compute='_compute_totals', store=True, string='Value at average cost')
    item_count = fields.Integer(compute='_compute_totals', string='Total items')
    totals = fields.Html(compute='_compute_totals', sanitize=False, string='Total quantity')
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    issued_by_id = fields.Many2one('res.users', readonly=True)
    approved_by_id = fields.Many2one('res.users', compute='_compute_approved_by', string='Approved by')
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('issued', 'Issued'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.depends('line_ids.qty', 'line_ids.product_id', 'line_ids.value')
    def _compute_totals(self):
        for issue in self:
            per_unit = {}
            for line in issue.line_ids:
                unit = line.product_id.uom_id.name or ''
                per_unit[unit] = per_unit.get(unit, 0.0) + line.qty
            issue.item_count = len(issue.line_ids)
            issue.totals = totals_html(per_unit)
            issue.amount = sum(issue.line_ids.mapped('value'))

    @api.depends('mos_approval_request_id.step_ids.decided_by_id')
    def _compute_approved_by(self):
        for issue in self:
            steps = issue.mos_approval_request_id.sudo().step_ids.filtered('decided_by_id').sorted('decided_at')
            issue.approved_by_id = steps[-1:].decided_by_id

    @api.constrains('kind', 'department_id', 'reason_id')
    def _check_destination(self):
        for issue in self:
            if issue.kind == 'consumption' and not issue.department_id:
                raise ValidationError(self.env._('Say which department the goods are issued to.'))
            if issue.kind == 'scrap' and not issue.reason_id:
                raise ValidationError(self.env._('Scrapping needs a reason.'))

    @api.constrains('location_id')
    def _check_location_available(self):
        for issue in self:
            if issue.location_id.mos_stock_status != 'available':
                raise ValidationError(self.env._('Only stock in an available location can be issued; %s is on hold.', issue.location_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.stock.issue')
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

    def _check_stock(self):
        for line in self.line_ids:
            free = self.env['stock.quant'].sudo()._get_available_quantity(line.product_id, self.location_id)
            if line.qty > free + 0.0005:
                raise UserError(self.env._('%(item)s: %(asked)s asked, only %(free)s available at %(place)s.', item=line.product_id.display_name, asked=line.qty, free=free, place=self.location_id.display_name))

    def action_submit(self):
        dialog = self._mos_attestation_dialog('action_submit')
        if dialog:
            return dialog
        for issue in self:
            if issue.state != 'draft':
                raise UserError(self.env._('Only a draft issue can be submitted.'))
            if not issue.line_ids:
                raise UserError(self.env._('List at least one product.'))
            issue._check_stock()
            if issue._mos_approval_required():
                issue.action_mos_submit_approval()
                issue.write({'state': 'pending'})
            else:
                issue.write({'state': 'approved', 'mos_approval_state': 'approved'})

    def action_issue(self):
        for issue in self:
            if issue.state != 'approved':
                raise UserError(self.env._('Only an approved issue can be posted to stock.'))
            if issue.requested_by_id == self.env.user and not issue.mos_approval_request_id:
                raise UserError(self.env._('An issue that needed no approval must be posted by someone other than its requester.'))
            issue._check_stock()
            destination = issue._destination_location()
            for line in issue.line_ids:
                issue._move(line.product_id, line.qty, issue.location_id, destination)
            issue.write({'state': 'issued', 'issued_by_id': self.env.uid})
            where = issue.department_id.display_name if issue.kind == 'consumption' else issue.reason_id.name
            self.env['mos.audit.event'].record('stock_issued', issue, f'{issue.name}: {len(issue.line_ids)} products, value {issue.amount:.2f}, {issue.kind} ({where})')

    def action_cancel(self):
        for issue in self:
            if issue.state in ('issued', 'rejected', 'cancelled'):
                raise UserError(self.env._('This issue is closed.'))
            if issue.state == 'pending':
                issue.action_mos_recall_approval()
            issue.write({'state': 'cancelled'})

    def _destination_location(self):
        self.ensure_one()
        xmlid = 'mos_inventory.location_scrapped' if self.kind == 'scrap' else 'mos_inventory.location_consumed'
        return self.env.ref(xmlid).sudo()

    def _move(self, product, qty, source, destination):
        move = self.env['stock.move'].sudo().create(
            {
                'product_id': product.id,
                'product_uom_qty': qty,
                'uom_id': product.uom_id.id,
                'location_id': source.id,
                'location_dest_id': destination.id,
                'origin': self.name,
                'quantity': qty,
                'picked': True,
            }
        )
        move._action_confirm()
        move._action_done()
        return move


class MosStockIssueLine(models.Model):
    _name = 'mos.stock.issue.line'
    _description = 'MOS goods issue line'

    issue_id = fields.Many2one('mos.stock.issue', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('is_storable', '=', True)]")
    code = fields.Char(related='product_id.default_code', string='Item code')
    uom_id = fields.Many2one(related='product_id.uom_id', string='Unit')
    qty = fields.Float(string='Quantity', required=True, digits=(14, 3))
    available_qty = fields.Float(compute='_compute_available', string='Available there', digits=(14, 3))
    unit_cost = fields.Float(related='product_id.standard_price', string='Average cost')
    value = fields.Float(compute='_compute_value', store=True, string='Value')

    @api.constrains('qty')
    def _check_qty(self):
        for line in self:
            if line.qty <= 0:
                raise ValidationError(self.env._('The quantity must be above zero.'))

    @api.depends('product_id', 'issue_id.location_id')
    def _compute_available(self):
        for line in self:
            location = line.issue_id.location_id
            line.available_qty = self.env['stock.quant'].sudo()._get_available_quantity(line.product_id, location) if (line.product_id and location) else 0.0

    @api.depends('qty', 'product_id.standard_price')
    def _compute_value(self):
        for line in self:
            line.value = line.qty * line.product_id.standard_price
