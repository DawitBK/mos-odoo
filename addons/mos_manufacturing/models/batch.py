from odoo import api, fields, models
from odoo.exceptions import UserError

KINDS = [('wheat', 'Wheat'), ('flour', 'Flour'), ('bran', 'Bran'), ('offal', 'Offal'), ('packed', 'Packed product')]


class MosBatch(models.Model):
    """A traceable quantity: wheat that came in on a delivery, flour or bran that came out of a milling run, bags that came
    out of a packing run. Every batch knows what it was made from and what was made from it, so a complaint about a bag of
    flour leads back to the wheat and the supplier, and a bad wheat lot leads forward to every bag and delivery (FR-MFG-009)."""

    _name = 'mos.batch'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS batch'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    kind = fields.Selection(KINDS, required=True)
    product_id = fields.Many2one('product.product', required=True)
    site_id = fields.Many2one('mos.site', required=True)
    qty = fields.Float(string='Quantity', required=True, digits=(14, 1))
    uom_id = fields.Many2one(related='product_id.uom_id')
    remaining_qty = fields.Float(compute='_compute_remaining', store=True, digits=(14, 1))
    silo_id = fields.Many2one('mos.silo', string='Silo (wheat)')
    visit_id = fields.Many2one('mos.gate.visit', string='Delivery')
    supplier_id = fields.Many2one('res.partner', compute='_compute_supplier', store=True)
    run_id = fields.Many2one('mos.milling.run', string='Milling run')
    pack_run_id = fields.Many2one('mos.pack.run', string='Packing run')
    state = fields.Selection([('hold', 'On hold for quality'), ('released', 'Released'), ('rejected', 'Rejected')], default='released', required=True, tracking=True)
    unit_cost = fields.Float(digits=(12, 4))
    parent_link_ids = fields.One2many('mos.batch.link', 'child_id', string='Made from')
    child_link_ids = fields.One2many('mos.batch.link', 'parent_id', string='Went into')
    delivery_ids = fields.One2many('mos.batch.delivery', 'batch_id', string='Delivered')
    inspection_ids = fields.One2many('mos.batch.inspection', 'batch_id')

    @api.depends('qty', 'child_link_ids.qty', 'delivery_ids.qty')
    def _compute_remaining(self):
        for batch in self:
            batch.remaining_qty = round(batch.qty - sum(batch.child_link_ids.mapped('qty')) - sum(batch.delivery_ids.mapped('qty')), 3)

    @api.depends('visit_id.partner_id')
    def _compute_supplier(self):
        for batch in self:
            batch.supplier_id = batch.visit_id.partner_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.batch')
        return super().create(vals_list)

    # -- creating batches -------------------------------------------------------------------------------------------
    @api.model
    def for_visit(self, visit):
        """The wheat batch of a received delivery: made once, whatever the number of calls."""
        existing = self.sudo().search([('visit_id', '=', visit.id)], limit=1)
        if existing:
            return existing
        insp = visit.inspection_id
        if not insp or insp.decision == 'reject':
            return self.browse()
        return self.sudo().create(
            {
                'kind': 'wheat',
                'product_id': insp.product_id.id,
                'site_id': visit.site_id.id,
                'qty': insp.payable_qty,
                'silo_id': visit.silo_id.id,
                'visit_id': visit.id,
                'unit_cost': (visit.sudo().purchase_id.order_line.filtered(lambda line: line.product_id == insp.product_id)[:1].price_unit),
            }
        )

    @api.model
    def consume(self, silo, product, qty, site):
        """Draw ``qty`` from the silo's wheat batches, oldest first. Stock the system never saw arrive (opened before batches)
        becomes an 'opening stock' batch, so the chain always adds up. Returns [(batch, quantity)]."""
        batches = self.sudo().search([('kind', '=', 'wheat'), ('silo_id', '=', silo.id), ('product_id', '=', product.id), ('remaining_qty', '>', 0), ('state', '=', 'released')], order='id')
        taken, left = [], qty
        for batch in batches:
            if left <= 0:
                break
            piece = min(batch.remaining_qty, left)
            taken.append((batch, piece))
            left -= piece
        if left > 0.0001:
            opening = self.sudo().create({'kind': 'wheat', 'product_id': product.id, 'site_id': site.id, 'qty': left, 'silo_id': silo.id, 'unit_cost': product.standard_price})
            opening.message_post(body=self.env._('Opening stock: the silo held more than the batches recorded.'))
            taken.append((opening, left))
        return taken

    # -- tracing ------------------------------------------------------------------------------------------------------
    def trace_back(self):
        """Everything this batch was made from, level by level: [(level, batch, quantity taken)]."""
        self.ensure_one()
        out, seen, frontier, level = [], {self.id}, [(self, 0.0)], 0
        while frontier:
            level += 1
            nxt = []
            for batch, _q in frontier:
                for link in batch.parent_link_ids:
                    if link.parent_id.id in seen:
                        continue
                    seen.add(link.parent_id.id)
                    out.append((level, link.parent_id, link.qty))
                    nxt.append((link.parent_id, link.qty))
            frontier = nxt
        return out

    def trace_forward(self):
        """Everything made from this batch and where it went: [(level, batch or None, quantity, note)]."""
        self.ensure_one()
        out, seen, frontier, level = [], {self.id}, [self], 0
        while frontier:
            level += 1
            nxt = []
            for batch in frontier:
                for link in batch.child_link_ids:
                    if link.child_id.id in seen:
                        continue
                    seen.add(link.child_id.id)
                    out.append((level, link.child_id, link.qty, ''))
                    nxt.append(link.child_id)
                for delivery in batch.delivery_ids:
                    out.append((level, None, delivery.qty, self.env._('delivered to %(customer)s on %(name)s', customer=delivery.customer_id.display_name or '?', name=delivery.delivery_id.name or '?')))
            frontier = nxt
        return out

    def action_open_trace(self):
        self.ensure_one()
        trace = self.env['mos.trace'].create({'batch_id': self.id})
        trace.action_build()
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.trace', 'res_id': trace.id, 'view_mode': 'form', 'target': 'current'}


class MosBatchLink(models.Model):
    """This much of one batch went into another."""

    _name = 'mos.batch.link'
    _description = 'MOS batch link'

    child_id = fields.Many2one('mos.batch', required=True, ondelete='cascade', index=True)
    parent_id = fields.Many2one('mos.batch', required=True, ondelete='restrict', index=True)
    qty = fields.Float(required=True, digits=(14, 3))


class MosBatchDelivery(models.Model):
    """This much of a batch left on a delivery order."""

    _name = 'mos.batch.delivery'
    _description = 'MOS batch delivery'

    batch_id = fields.Many2one('mos.batch', ondelete='restrict', index=True, help='Empty when the quantity could not be tied to a batch.')
    delivery_id = fields.Many2one('mos.delivery.order', required=True, ondelete='restrict')
    customer_id = fields.Many2one(related='delivery_id.partner_id', store=True)
    qty = fields.Float(required=True, digits=(14, 3))
    untraced = fields.Boolean(compute='_compute_untraced', store=True)

    @api.depends('batch_id')
    def _compute_untraced(self):
        for rec in self:
            rec.untraced = not rec.batch_id


class MosTrace(models.TransientModel):
    """Trace one batch back to its wheat and forward to its customers (FR-MFG-009)."""

    _name = 'mos.trace'
    _description = 'MOS batch trace'

    batch_id = fields.Many2one('mos.batch', required=True)
    line_ids = fields.One2many('mos.trace.line', 'trace_id')

    def action_build(self):
        self.ensure_one()
        self.line_ids.unlink()
        rows = []
        for level, batch, qty in self.batch_id.trace_back():
            note = ''
            if batch.visit_id:
                note = self.env._('delivery %(visit)s from %(supplier)s', visit=batch.visit_id.name, supplier=batch.supplier_id.display_name or '?')
            elif batch.run_id:
                note = self.env._('milling run %s', batch.run_id.name)
            elif batch.pack_run_id:
                note = self.env._('packing run %s', batch.pack_run_id.name)
            elif batch.kind == 'wheat':
                note = self.env._('opening stock')
            rows.append({'trace_id': self.id, 'direction': 'back', 'level': level, 'batch_id': batch.id, 'qty': qty, 'note': note})
        for level, batch, qty, note in self.batch_id.trace_forward():
            if batch:
                if batch.run_id:
                    note = self.env._('milling run %s', batch.run_id.name)
                elif batch.pack_run_id:
                    note = self.env._('packing run %s', batch.pack_run_id.name)
            rows.append({'trace_id': self.id, 'direction': 'forward', 'level': level, 'batch_id': batch.id if batch else False, 'qty': qty, 'note': note})
        self.env['mos.trace.line'].create(rows)
        return True


class MosTraceLine(models.TransientModel):
    _name = 'mos.trace.line'
    _description = 'MOS batch trace line'
    _order = 'direction desc, level, id'

    trace_id = fields.Many2one('mos.trace', required=True, ondelete='cascade')
    direction = fields.Selection([('back', 'Made from'), ('forward', 'Went on to')], required=True)
    level = fields.Integer()
    batch_id = fields.Many2one('mos.batch')
    product_id = fields.Many2one(related='batch_id.product_id')
    qty = fields.Float(digits=(14, 1))
    note = fields.Char()


class MosBatchInspection(models.Model):
    """Finished-goods quality control of a batch, against the specification in force for its product. Whoever tested does not
    decide. A released batch is available to sell; a rejected one is blocked (FR-MFG-008, FR-QC-019)."""

    _name = 'mos.batch.inspection'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS batch inspection'
    _order = 'id desc'
    _mos_final_states = ('decided',)

    name = fields.Char(readonly=True, copy=False, default='New')
    batch_id = fields.Many2one('mos.batch', required=True, ondelete='restrict')
    product_id = fields.Many2one(related='batch_id.product_id', store=True)
    spec_id = fields.Many2one('mos.lab.spec', required=True, readonly=True)
    result_ids = fields.One2many('mos.batch.result', 'inspection_id')
    state = fields.Selection([('open', 'Open'), ('tested', 'Tested'), ('decided', 'Decided')], default='open', required=True, readonly=True, tracking=True)
    suggested = fields.Selection([('incomplete', 'Incomplete'), ('pass', 'Pass'), ('fail', 'Fail')], compute='_compute_suggested', store=True)
    tested_by_id = fields.Many2one('res.users', readonly=True)
    decision = fields.Selection([('release', 'Release'), ('reject', 'Reject')], readonly=True)
    decided_by_id = fields.Many2one('res.users', readonly=True)
    note = fields.Char(readonly=True)

    @api.depends('result_ids.value', 'result_ids.line_id', 'spec_id.line_ids')
    def _compute_suggested(self):
        for insp in self:
            lines = insp.spec_id.line_ids
            have = {r.line_id: r for r in insp.result_ids}
            if not lines or any(line not in have for line in lines):
                insp.suggested = 'incomplete'
            else:
                insp.suggested = 'pass' if all(have[line].passed for line in lines) else 'fail'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            batch = self.env['mos.batch'].browse(vals['batch_id'])
            if batch.kind == 'wheat':
                raise UserError(self.env._('Wheat is inspected on delivery, not as a finished product.'))
            if not vals.get('spec_id'):
                spec = self.env['mos.lab.spec'].in_force(batch.product_id)
                if not spec:
                    raise UserError(self.env._('There is no approved specification for %s.', batch.product_id.display_name))
                vals['spec_id'] = spec.id
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.batch.inspection')
        return super().create(vals_list)

    def record_result(self, line, value):
        self.ensure_one()
        if self.state == 'decided' or line.spec_id != self.spec_id:
            raise UserError(self.env._('That result cannot be entered on this inspection.'))
        self.write({'state': 'open'})
        existing = self.result_ids.filtered(lambda r: r.line_id == line)
        if existing:
            existing.write({'value': value})
            return existing
        return self.env['mos.batch.result'].create({'inspection_id': self.id, 'line_id': line.id, 'value': value})

    def action_submit(self):
        for insp in self:
            if insp.suggested == 'incomplete':
                raise UserError(self.env._('Every parameter needs a result.'))
            insp.write({'state': 'tested', 'tested_by_id': self.env.uid})

    def decide(self, decision, note=''):
        self.ensure_one()
        user = self.env.user
        if not (user.has_group('mos_lab.group_mos_lab_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a lab manager can release or reject a batch.'))
        if self.state != 'tested':
            raise UserError(self.env._('Submit the results first.'))
        if user.id == self.tested_by_id.id:
            raise UserError(self.env._('The release decision must be made by someone other than the person who entered the results.'))
        if decision == 'release' and self.suggested == 'fail' and not note:
            raise UserError(self.env._('Releasing a batch that failed its specification needs a written justification.'))
        self.write({'state': 'decided', 'decision': decision, 'decided_by_id': user.id, 'note': note})
        self.batch_id.sudo()._apply_decision(self)
        self.env['mos.audit.event'].record('batch_decided', self, f'{self.batch_id.name}: {decision}')


class MosBatchResult(models.Model):
    _name = 'mos.batch.result'
    _description = 'MOS batch inspection result'

    inspection_id = fields.Many2one('mos.batch.inspection', required=True, ondelete='cascade')
    line_id = fields.Many2one('mos.lab.spec.line', required=True, string='Parameter')
    value = fields.Float(digits=(12, 3), required=True)
    passed = fields.Boolean(compute='_compute_passed', store=True)

    @api.depends('value', 'line_id.min_value', 'line_id.max_value')
    def _compute_passed(self):
        for r in self:
            r.passed = r.line_id.min_value <= r.value <= r.line_id.max_value

    _line_once = models.Constraint('unique(inspection_id, line_id)', 'One result per parameter.')
