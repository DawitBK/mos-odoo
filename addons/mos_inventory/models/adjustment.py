from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosStockAdjustment(models.Model):
    """Correct the book stock to what was counted, for a reason, with approval above the matrix threshold (FR-INV-013..016)."""

    _name = 'mos.stock.adjustment'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS stock adjustment'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state', 'applied_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    location_id = fields.Many2one('stock.location', required=True, domain="[('usage', '=', 'internal')]")
    reason_id = fields.Many2one('mos.reason', required=True, domain=[('domain', 'in', ('adjustment', 'count', 'scrap', 'transit_loss'))])
    note = fields.Char()
    date = fields.Date(default=fields.Date.context_today, required=True)
    line_ids = fields.One2many('mos.stock.adjustment.line', 'adjustment_id', string='Products')
    amount = fields.Float(compute='_compute_amount', store=True, string='Value of the change')
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    applied_by_id = fields.Many2one('res.users', readonly=True)
    cycle_count_id = fields.Many2one('mos.cycle.count', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('applied', 'Applied'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.depends('line_ids.delta', 'line_ids.product_id')
    def _compute_amount(self):
        for adj in self:
            adj.amount = sum(abs(line.delta) * line.product_id.standard_price for line in adj.line_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.stock.adjustment')
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
        for adj in self:
            if adj.state != 'draft':
                raise UserError(self.env._('Only a draft adjustment can be submitted.'))
            if not adj.line_ids:
                raise UserError(self.env._('List at least one product.'))
            if not any(line.delta for line in adj.line_ids):
                raise UserError(self.env._('Nothing changes: every counted quantity equals the book quantity.'))
            if adj._mos_approval_required():
                adj.action_mos_submit_approval()
                adj.write({'state': 'pending'})
            else:
                adj.write({'state': 'approved', 'mos_approval_state': 'approved'})

    def action_apply(self):
        for adj in self:
            if adj.state != 'approved':
                raise UserError(self.env._('Only an approved adjustment can be applied.'))
            if adj.requested_by_id == self.env.user and not adj.mos_approval_request_id:
                raise UserError(self.env._('An adjustment that needed no approval must be applied by someone other than its requester.'))
            for line in adj.line_ids.filtered('delta'):
                quant = self.env['stock.quant'].sudo().with_context(inventory_mode=True).create(
                    {'product_id': line.product_id.id, 'location_id': adj.location_id.id, 'inventory_quantity': line.new_qty}
                )
                quant.action_apply_inventory()
            adj.write({'state': 'applied', 'applied_by_id': self.env.uid})
            self.env['mos.audit.event'].record('stock_adjusted', adj, f'{adj.name}: {len(adj.line_ids)} products, value {adj.amount:.2f}, {adj.reason_id.name}')

    def action_cancel(self):
        for adj in self:
            if adj.state in ('applied', 'rejected', 'cancelled'):
                raise UserError(self.env._('This adjustment is closed.'))
            if adj.state == 'pending':
                adj.action_mos_recall_approval()
            adj.write({'state': 'cancelled'})


class MosStockAdjustmentLine(models.Model):
    _name = 'mos.stock.adjustment.line'
    _description = 'MOS stock adjustment line'

    adjustment_id = fields.Many2one('mos.stock.adjustment', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('is_storable', '=', True)]")
    system_qty = fields.Float(string='Book quantity', readonly=True, digits=(14, 1))
    new_qty = fields.Float(string='Counted quantity', required=True, digits=(14, 1))
    delta = fields.Float(compute='_compute_delta', store=True, digits=(14, 1))

    @api.depends('system_qty', 'new_qty')
    def _compute_delta(self):
        for line in self:
            line.delta = line.new_qty - line.system_qty

    @api.constrains('new_qty')
    def _check_qty(self):
        for line in self:
            if line.new_qty < 0:
                raise ValidationError(self.env._('A counted quantity cannot be negative.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            adj = self.env['mos.stock.adjustment'].browse(vals['adjustment_id'])
            if adj.state != 'draft':
                raise UserError(self.env._('Lines of a submitted adjustment cannot change.'))
            if 'system_qty' not in vals:
                quants = self.env['stock.quant'].sudo().search([('product_id', '=', vals['product_id']), ('location_id', '=', adj.location_id.id)])
                vals['system_qty'] = sum(quants.mapped('quantity'))
        return super().create(vals_list)

    def write(self, vals):
        if any(line.adjustment_id.state != 'draft' for line in self):
            raise UserError(self.env._('Lines of a submitted adjustment cannot change.'))
        return super().write(vals)

    def unlink(self):
        if any(line.adjustment_id.state != 'draft' for line in self):
            raise UserError(self.env._('Lines of a submitted adjustment cannot change.'))
        return super().unlink()


class MosCycleCount(models.Model):
    """Count part of the stock on a schedule instead of everything at year end. The sheet freezes the book quantity;
    differences beyond the tolerance become an adjustment that goes through approval (FR-INV-017..019)."""

    _name = 'mos.cycle.count'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS cycle count'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    location_id = fields.Many2one('stock.location', required=True, domain="[('usage', '=', 'internal')]")
    counter_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    line_ids = fields.One2many('mos.cycle.count.line', 'count_id', string='Sheet')
    adjustment_id = fields.Many2one('mos.stock.adjustment', readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('counting', 'Counting'), ('submitted', 'Submitted'), ('closed', 'Closed')], default='draft', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.cycle.count')
        return super().create(vals_list)

    def action_start(self):
        for count in self:
            if count.state != 'draft':
                raise UserError(self.env._('Only a draft count can start.'))
            quants = self.env['stock.quant'].sudo().search([('location_id', '=', count.location_id.id), ('quantity', '!=', 0)])
            if not quants:
                raise UserError(self.env._('There is no stock in %s to count.', count.location_id.display_name))
            by_product = {}
            for quant in quants:
                by_product[quant.product_id] = by_product.get(quant.product_id, 0.0) + quant.quantity
            count.write({'state': 'counting', 'line_ids': [Command.create({'product_id': p.id, 'system_qty': q}) for p, q in by_product.items()]})

    def action_submit(self):
        tolerance = self.env['ir.config_parameter'].sudo().get_float('mos_inventory.count_tolerance_pct', 0.5)
        for count in self:
            if count.state != 'counting':
                raise UserError(self.env._('Only a count in progress can be submitted.'))
            if any(not line.counted for line in count.line_ids):
                raise UserError(self.env._('Count every product on the sheet first.'))
            reason = self.env['mos.reason'].sudo().search([('domain', '=', 'count')], limit=1)
            if not reason:
                reason = self.env['mos.reason'].sudo().create({'domain': 'count', 'code': 'COUNT', 'name': self.env._('Count difference')})
            off = count.line_ids.filtered(lambda line: line.system_qty and abs(line.counted_qty - line.system_qty) / abs(line.system_qty) * 100.0 > tolerance or (not line.system_qty and line.counted_qty))
            adjustment = False
            if off:
                adjustment = self.env['mos.stock.adjustment'].create(
                    {
                        'site_id': count.site_id.id,
                        'location_id': count.location_id.id,
                        'reason_id': reason.id,
                        'note': count.name,
                        'cycle_count_id': count.id,
                        'line_ids': [Command.create({'product_id': line.product_id.id, 'system_qty': line.system_qty, 'new_qty': line.counted_qty}) for line in off],
                    }
                )
                adjustment.action_submit()
            count.write({'state': 'submitted', 'adjustment_id': adjustment.id if adjustment else False})

    def action_close(self):
        for count in self:
            if count.state != 'submitted':
                raise UserError(self.env._('Submit the count first.'))
            if count.adjustment_id and count.adjustment_id.state not in ('applied', 'rejected', 'cancelled'):
                raise UserError(self.env._('The adjustment %s is still open.', count.adjustment_id.name))
            count.state = 'closed'


class MosCycleCountLine(models.Model):
    _name = 'mos.cycle.count.line'
    _description = 'MOS cycle count line'

    count_id = fields.Many2one('mos.cycle.count', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True)
    system_qty = fields.Float(readonly=True, digits=(14, 1))
    counted = fields.Boolean(readonly=True)
    counted_qty = fields.Float(digits=(14, 1))
    variance = fields.Float(compute='_compute_variance', digits=(14, 1))

    @api.depends('system_qty', 'counted_qty', 'counted')
    def _compute_variance(self):
        for line in self:
            line.variance = line.counted_qty - line.system_qty if line.counted else 0.0

    def write(self, vals):
        if 'counted_qty' in vals:
            if any(line.count_id.state != 'counting' for line in self):
                raise UserError(self.env._('The sheet is not open for counting.'))
            if vals['counted_qty'] < 0:
                raise ValidationError(self.env._('A counted quantity cannot be negative.'))
            vals['counted'] = True
        return super().write(vals)
