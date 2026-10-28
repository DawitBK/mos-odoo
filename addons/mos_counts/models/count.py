from odoo import api, fields, models
from odoo.exceptions import UserError


class MosCycleCount(models.Model):
    """What the client's count screens add to a cycle count (FR-INV-009): the counter does not see the book quantity (blind), a sheet
    can be printed or filled from a spreadsheet, a line that differs by more than the recount limit is counted again by somebody else,
    and the count can go to a reviewer who approves it or asks for changes.

    The recount limit is ``mos_counts.recount_pct`` (0 = off) and the review is switched on with ``mos_counts.require_review``.
    Both are off by default; blind counting is on unless ``mos_counts.blind_by_default`` says otherwise."""

    _inherit = 'mos.cycle.count'

    state = fields.Selection(selection_add=[('counting',), ('review', 'In review')], ondelete={'review': 'set default'})
    blind = fields.Boolean(string='Blind count', default=lambda self: self.env['ir.config_parameter'].sudo().get_bool('mos_counts.blind_by_default', True), help='The counter does not see the book quantity.')
    reviewer_id = fields.Many2one('res.users', readonly=True)
    review_required = fields.Boolean(compute='_compute_review_required')
    review_note = fields.Text(readonly=True)
    hide_book = fields.Boolean(compute='_compute_hide_book', help='The book quantity is hidden while the count is open and blind.')
    recount_lines = fields.Integer(compute='_compute_recount_lines')
    matched_lines = fields.Integer(compute='_compute_matched')
    variance_lines = fields.Integer(compute='_compute_matched')

    def _compute_review_required(self):
        required = self.env['ir.config_parameter'].sudo().get_bool('mos_counts.require_review')
        for count in self:
            count.review_required = required

    @api.depends('blind', 'state')
    def _compute_hide_book(self):
        for count in self:
            count.hide_book = count.blind and count.state == 'counting'

    @api.depends('line_ids.recount')
    def _compute_recount_lines(self):
        for count in self:
            count.recount_lines = len(count.line_ids.filtered('recount'))

    @api.depends('line_ids.counted', 'line_ids.variance')
    def _compute_matched(self):
        for count in self:
            counted = count.line_ids.filtered('counted')
            count.matched_lines = len(counted.filtered(lambda l: abs(l.variance) < 0.05))
            count.variance_lines = len(counted) - count.matched_lines

    # -- the steps ----------------------------------------------------------------------------------------------------
    def _recount_limit(self):
        return self.env['ir.config_parameter'].sudo().get_float('mos_counts.recount_pct', 0.0)

    def action_send_review(self):
        """The counter is done. Lines that differ by more than the limit are first counted again by somebody else."""
        limit = self._recount_limit()
        for count in self:
            if count.state != 'counting':
                raise UserError(self.env._('Only a count in progress can be sent for review.'))
            if any(not line.counted for line in count.line_ids):
                raise UserError(self.env._('Count every product on the sheet first.'))
            if limit > 0:
                far = count.line_ids.filtered(lambda l: not l.recounted and (abs(l.variance) / abs(l.system_qty) * 100.0 > limit if l.system_qty else abs(l.variance) > 0))
                if far:
                    far.sudo().write({'recount': True, 'counted': False})
                    count.message_post(body=self.env._('%(n)s line(s) differ by more than %(limit)s%% and must be counted again by somebody else.', n=len(far), limit=limit))
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {'title': self.env._('Recount needed'), 'message': self.env._('%s line(s) differ by more than the limit: somebody else counts them again.', len(far)), 'type': 'warning', 'sticky': False},
                    }
            count.write({'state': 'review'})
            count.message_post(body=self.env._('Sent for review by %s.', self.env.user.name))

    def action_review_approve(self):
        for count in self:
            if count.state != 'review':
                raise UserError(self.env._('Only a count in review can be approved.'))
            if self.env.user == count.counter_id or self.env.user in count.line_ids.mapped('first_counter_id'):
                raise UserError(self.env._('The count is reviewed by somebody who did not count.'))
            count.sudo().write({'state': 'counting', 'reviewer_id': self.env.uid, 'review_note': False})
            count.with_context(mos_count_reviewed=True).action_submit()
            count.message_post(body=self.env._('Count approved by %s.', self.env.user.name))

    def action_submit(self):
        if self.env['ir.config_parameter'].sudo().get_bool('mos_counts.require_review') and not self.env.context.get('mos_count_reviewed'):
            if any(count.state == 'counting' for count in self):
                raise UserError(self.env._('Send the count for review first: a reviewer approves it before the adjustment is made.'))
        return super().action_submit()

    def action_request_changes(self, note, lines):
        for count in self:
            if count.state != 'review':
                raise UserError(self.env._('Only a count in review can be sent back.'))
            if not (note or '').strip():
                raise UserError(self.env._('Say what has to change: a reason is required to ask for changes.'))
            if self.env.user == count.counter_id:
                raise UserError(self.env._('The count is reviewed by somebody who did not count.'))
            if not lines:
                raise UserError(self.env._('Choose the lines that have to be counted again.'))
            lines.sudo().write({'recount': True, 'counted': False})
            count.sudo().write({'state': 'counting', 'review_note': note, 'reviewer_id': self.env.uid})
            count.message_post(body=self.env._('%(who)s asked for changes: %(note)s', who=self.env.user.name, note=note))

    def action_request_changes_dialog(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Request changes'),
            'res_model': 'mos.count.changes',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_count_id': self.id},
        }

    def action_import_dialog(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Import counts from a spreadsheet'),
            'res_model': 'mos.count.import',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_count_id': self.id},
        }

    def action_print_sheet(self):
        self.ensure_one()
        return self.env.ref('mos_counts.action_report_count_sheet').report_action(self)


class MosCycleCountLine(models.Model):
    _inherit = 'mos.cycle.count.line'

    code = fields.Char(related='product_id.default_code', string='Item code')
    uom_id = fields.Many2one(related='product_id.uom_id')
    first_counter_id = fields.Many2one('res.users', readonly=True)
    counted_by_id = fields.Many2one('res.users', readonly=True)
    recount = fields.Boolean(string='Count again', readonly=True)
    recounted = fields.Boolean(readonly=True)
    previous_qty = fields.Float(string='First count', readonly=True, digits=(14, 1))

    def write(self, vals):
        if 'counted_qty' not in vals:
            return super().write(vals)
        for line in self:
            if line.count_id.state != 'counting':
                raise UserError(self.env._('The sheet is not open for counting.'))
            if line.recount and line.first_counter_id == self.env.user:
                raise UserError(self.env._('%s must be counted again by somebody other than the person who counted it first.', line.product_id.display_name))
            extra = {'counted_by_id': self.env.uid}
            if not line.first_counter_id:
                extra['first_counter_id'] = self.env.uid
            if line.recount:
                extra.update({'recount': False, 'recounted': True, 'previous_qty': line.counted_qty})
            super(MosCycleCountLine, line).write({**vals, **extra})
        return True
