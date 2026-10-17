from odoo import api, fields, models
from odoo.exceptions import UserError


class MosWeighTicket(models.Model):
    """Weigh ticket: first and second weighing, blind second-person verification, superseding re-weigh.

    Inbound loaded truck: first weighing is gross, second is tare (FR-GATE-011). A posted ticket is never
    changed or deleted; a re-weigh creates a new ticket that supersedes it, with a reason (FR-GATE-014).
    """

    _name = 'mos.weigh.ticket'
    _inherit = ['mos.posted']
    _description = 'MOS weigh ticket'
    _order = 'id'
    _mos_final_states = ('posted', 'superseded')
    _mos_editable_after_post = {'state', 'superseded_by_id'}

    name = fields.Char(string='Ticket number', readonly=True, copy=False, default='New')
    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='restrict', index=True)
    site_id = fields.Many2one(related='visit_id.site_id', store=True)
    state = fields.Selection(
        [('draft', 'Weighing'), ('weighed', 'Awaiting verification'), ('posted', 'Posted'), ('superseded', 'Superseded')],
        default='draft',
        required=True,
        readonly=True,
    )
    first_weight = fields.Float(string='First weighing (gross, kg)', digits=(12, 1), readonly=True)
    first_by_id = fields.Many2one('res.users', readonly=True)
    second_weight = fields.Float(string='Second weighing (tare, kg)', digits=(12, 1), readonly=True)
    second_by_id = fields.Many2one('res.users', readonly=True)
    gross_weight = fields.Float(digits=(12, 1), store=True, compute='_compute_net')
    tare_weight = fields.Float(digits=(12, 1), store=True, compute='_compute_net')
    net_weight = fields.Float(string='Net weight (kg)', digits=(12, 1), store=True, compute='_compute_net')
    verified_by_id = fields.Many2one('res.users', readonly=True)
    verify_attempts = fields.Integer(readonly=True)
    supersedes_id = fields.Many2one('mos.weigh.ticket', readonly=True)
    superseded_by_id = fields.Many2one('mos.weigh.ticket', readonly=True)
    reweigh_reason_id = fields.Many2one('mos.reason', domain=[('domain', '=', 'reweigh')], readonly=True)

    @api.depends('first_weight', 'second_weight')
    def _compute_net(self):
        for t in self:
            t.gross_weight = t.first_weight
            t.tare_weight = t.second_weight
            t.net_weight = t.first_weight - t.second_weight if t.first_weight and t.second_weight else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.weigh.ticket')
        return super().create(vals_list)

    # -- weighings -------------------------------------------------------------------------------
    def record_first(self, weight):
        self.ensure_one()
        if self.state != 'draft' or self.first_weight:
            raise UserError(self.env._('The first weighing is already recorded.'))
        if weight <= 0:
            raise UserError(self.env._('A weight must be above zero.'))
        self.write({'first_weight': weight, 'first_by_id': self.env.uid})

    def record_second(self, weight):
        self.ensure_one()
        if self.state != 'draft' or not self.first_weight or self.second_weight:
            raise UserError(self.env._('Record the first weighing before the second, and only once.'))
        if weight < 0 or weight >= self.first_weight:
            raise UserError(
                self.env._('Inbound: the tare (%(tare)s kg) must be lower than the gross (%(gross)s kg).', tare=weight, gross=self.first_weight)
            )
        self.write({'second_weight': weight, 'second_by_id': self.env.uid, 'state': 'weighed'})

    def verify_blind(self, weight_gross, weight_tare):
        """A different person re-enters both weights without seeing them; a mismatch is refused and counted (FR-GATE-010)."""
        self.ensure_one()
        if self.state != 'weighed':
            raise UserError(self.env._('Nothing to verify: the ticket is %s.', self.state))
        if self.env.uid in (self.first_by_id.id, self.second_by_id.id):
            raise UserError(self.env._('Verification must be done by a different person than the one who weighed.'))
        tolerance = self.env['ir.config_parameter'].sudo().get_float('mos_gate.verify_tolerance_kg', 0.0)
        if abs(weight_gross - self.first_weight) > tolerance or abs(weight_tare - self.second_weight) > tolerance:
            # The counter is written on a separate cursor so it survives the refusal's rollback.
            with self.pool.cursor() as cr:
                cr.execute('UPDATE mos_weigh_ticket SET verify_attempts = verify_attempts + 1 WHERE id = %s', [self.id])
            raise UserError(self.env._('The verification weights do not match the recorded weighings.'))
        self.write({'verified_by_id': self.env.uid})
        self.write({'state': 'posted'})
        self.env['mos.audit.event'].record('weigh_ticket_posted', self, f'{self.name}: net {self.net_weight} kg')
        self.visit_id.sudo().write({'state': 'weighed'})

    # -- corrections -----------------------------------------------------------------------------
    def reweigh(self, reason):
        """Supersede a posted ticket: the original stays, a new ticket takes over (FR-GATE-014)."""
        self.ensure_one()
        if self.state != 'posted':
            raise UserError(self.env._('Only a posted ticket can be superseded.'))
        if not reason or reason.domain != 'reweigh':
            raise UserError(self.env._('A re-weigh reason code is required.'))
        new = self.create({'visit_id': self.visit_id.id, 'supersedes_id': self.id, 'reweigh_reason_id': reason.id})
        self.write({'state': 'superseded', 'superseded_by_id': new.id})
        self.visit_id.sudo().write({'state': 'weighing'})
        self.env['mos.audit.event'].record('weigh_ticket_superseded', self, f'{self.name} -> {new.name}: {reason.name}')
        return new
