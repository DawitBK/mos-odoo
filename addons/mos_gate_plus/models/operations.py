from psycopg2.errors import ForeignKeyViolation

from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError


class MosGateTolerance(models.Model):
    """How far a net weight may be from what was ordered before the gate reacts. The most specific row wins:
    product, then category, then partner, then site and purpose alone (FR-GATE-012)."""

    _name = 'mos.gate.tolerance'
    _inherit = ['mos.audited']
    _description = 'MOS gate weight tolerance'
    _order = 'id'

    site_id = fields.Many2one('mos.site')
    purpose = fields.Selection([('inbound_po', 'Inbound: PO delivery'), ('outbound_do', 'Outbound: customer delivery')])
    categ_id = fields.Many2one('product.category', string='Product category')
    product_id = fields.Many2one('product.product')
    partner_id = fields.Many2one('res.partner', string='Supplier or customer')
    pct = fields.Float(string='Tolerance (%)', required=True, default=2.0)
    action = fields.Selection([('warn', 'Warn (opens an incident)'), ('block', 'Block exit until a supervisor approves')], default='warn', required=True)
    active = fields.Boolean(default=True)

    @api.constrains('pct')
    def _check(self):
        for rec in self:
            if rec.pct < 0:
                raise ValidationError(self.env._('A tolerance cannot be negative.'))

    @api.model
    def best_for(self, visit, product):
        """The most specific tolerance row that applies to this visit and product, or an empty record."""
        best, best_score = self.browse(), -1
        for row in self.sudo().search([]):
            score = 0
            if row.site_id:
                if row.site_id != visit.site_id:
                    continue
                score += 1
            if row.purpose:
                if row.purpose != visit.purpose:
                    continue
                score += 1
            if row.partner_id:
                if row.partner_id != visit.partner_id:
                    continue
                score += 2
            if row.categ_id:
                categs, categ = set(), product.categ_id
                while categ:
                    categs.add(categ.id)
                    categ = categ.parent_id
                if row.categ_id.id not in categs:
                    continue
                score += 4
            if row.product_id:
                if row.product_id != product:
                    continue
                score += 8
            if score > best_score:
                best, best_score = row, score
        return best


class MosWeighTicket(models.Model):
    """A ticket that lands outside the tolerance is flagged at posting (FR-GATE-012)."""

    _inherit = 'mos.weigh.ticket'

    _mos_editable_after_post = {'state', 'superseded_by_id', 'photo_pending', 'photo_pending_at', 'tolerance_note'}

    tolerance_note = fields.Char(readonly=True)

    def verify_blind(self, weight_gross, weight_tare):
        result = super().verify_blind(weight_gross, weight_tare)
        for ticket in self:
            ticket._check_tolerance()
        return result

    def _check_tolerance(self):
        self.ensure_one()
        visit = self.visit_id.sudo()
        product, expected = self._expected_quantity(visit)
        if not product or not expected:
            return
        row = self.env['mos.gate.tolerance'].best_for(visit, product)
        if not row:
            return
        deviation = (self.net_weight - expected) / expected * 100.0
        if abs(deviation) <= row.pct + 0.0001:
            return
        note = self.env._('net %(net)s kg against %(exp)s kg expected: %(dev).1f%%, tolerance %(tol)s%%', net=self.net_weight, exp=round(expected), dev=deviation, tol=row.pct)
        self.sudo().write({'tolerance_note': note})
        self.env['mos.gate.incident'].sudo().create({'site_id': visit.site_id.id, 'visit_id': visit.id, 'kind': 'other', 'severity': 'high' if row.action == 'block' else 'medium', 'description': f'{self.name}: {note}'})
        if row.action == 'block':
            visit.write({'tolerance_blocked': True})

    def _expected_quantity(self, visit):
        """What the paperwork says should be on the truck: the open quantity of the order line, or the ordered quantity of the delivery."""
        if visit.purpose == 'inbound_po' and visit.purchase_id:
            try:
                product = visit._po_product()
            except UserError:
                return False, 0.0
            lines = visit.purchase_id.order_line.filtered(lambda l: l.product_id == product)
            return product, max(sum(lines.mapped('product_qty')) - sum(lines.mapped('qty_received')), 0.0)
        delivery = self.env['mos.delivery.order'].sudo().search([('visit_id', '=', visit.id)], limit=1)
        if delivery:
            return delivery.product_id, delivery.ordered_qty
        return False, 0.0


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    tolerance_blocked = fields.Boolean(readonly=True)

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        if self.tolerance_blocked:
            problems.append(self.env._('the weight is outside the tolerance (a gate supervisor must approve it)'))
        return problems

    def action_approve_tolerance(self, reason):
        user = self.env.user
        if not (user.has_group('mos_flows.group_mos_gate_supervisor') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a gate supervisor can approve a weight outside the tolerance.'))
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        for visit in self:
            visit.sudo().write({'tolerance_blocked': False})
            self.env['mos.audit.event'].record('tolerance_approved', visit, f'{visit.name}: {reason}')


class MosClearanceAttempt(models.Model):
    """Every try to clear a vehicle for exit, with what stopped it. Written on its own cursor so a refusal still leaves its record (FR-GATE-016)."""

    _name = 'mos.gate.clearance'
    _description = 'MOS exit clearance attempt'
    _order = 'id desc'
    _log_access = False

    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='cascade', index=True)
    user_id = fields.Many2one('res.users')
    at = fields.Datetime(default=fields.Datetime.now)
    result = fields.Selection([('cleared', 'Cleared'), ('refused', 'Refused')], required=True)
    reasons = fields.Text()

    def unlink(self):
        raise UserError(self.env._('The clearance log is never edited or deleted.'))

    def write(self, vals):
        raise UserError(self.env._('The clearance log is never edited or deleted.'))


class MosGateVisitClearance(models.Model):
    _inherit = 'mos.gate.visit'

    clearance_ids = fields.One2many('mos.gate.clearance', 'visit_id')

    def action_clear_exit(self):
        for visit in self:
            try:
                super(MosGateVisitClearance, visit).action_clear_exit()
            except UserError as error:
                visit._log_clearance('refused', str(error.args[0]))
                raise
            visit._log_clearance('cleared', '')
        return True

    def _log_clearance(self, result, reasons):
        """Record the attempt. A refusal rolls the request back, so the row is written on its own cursor to survive it."""
        self.ensure_one()
        row = {'visit_id': self.id, 'user_id': self.env.uid, 'result': result, 'reasons': reasons}
        if tools.config.get('test_enable') or result == 'cleared':
            # Test transactions are rolled back as a whole and a second connection cannot see their uncommitted rows;
            # a cleared exit is not rolled back, so it is written in the same transaction as the visit it belongs to.
            self.env['mos.gate.clearance'].sudo().create(row)
        else:
            try:
                with self.pool.cursor() as cr:
                    cr.execute(
                        "INSERT INTO mos_gate_clearance (visit_id, user_id, at, result, reasons) VALUES (%s, %s, NOW() AT TIME ZONE 'UTC', %s, %s)",
                        [self.id, self.env.uid, result, reasons],
                    )
            except ForeignKeyViolation:
                # The visit itself is not committed yet (a script, not a request): the row goes in the caller's transaction.
                self.env['mos.gate.clearance'].sudo().create(row)
        self.invalidate_recordset(['clearance_ids'])
