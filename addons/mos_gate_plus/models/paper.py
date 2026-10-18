from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_weight_basis = fields.Selection(
        [('bridge', 'Our bridge weight'), ('declared', "The supplier's declared weight"), ('lower', 'The lower of the two')],
        string='Payable weight basis',
        default='bridge',
        help='Which weight a delivery from this supplier is received and paid at (FR-GATE-015).',
    )


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    weight_basis = fields.Selection(selection_add=[('lower', 'Lower of the two')], ondelete={'lower': 'set default'})

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'weight_basis' not in vals and vals.get('partner_id'):
                basis = self.env['res.partner'].browse(vals['partner_id']).mos_weight_basis
                if basis:
                    vals['weight_basis'] = basis
        return super().create(vals_list)


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    @api.depends('net_weight', 'deduction_pct', 'decision', 'visit_id.received_qty', 'visit_id.flow', 'visit_id.weight_basis', 'visit_id.declared_qty')
    def _compute_payable(self):
        super()._compute_payable()
        for insp in self:
            visit = insp.visit_id
            if insp.decision != 'reject' and visit.weight_basis == 'lower' and visit.declared_qty and visit.flow != 'B':
                base = min(insp.net_weight, visit.declared_qty)
                insp.payable_qty = round(base * (1 - insp.deduction_pct / 100.0), 1)


class MosPaperBook(models.Model):
    """A book of pre-numbered paper slips for when the system or the network is down. Every number is accounted for (FR-GATE-023)."""

    _name = 'mos.paper.book'
    _inherit = ['mos.audited']
    _description = 'MOS paper slip book'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True)
    prefix = fields.Char(default='PS-')
    first_no = fields.Integer(required=True)
    last_no = fields.Integer(required=True)
    slip_ids = fields.One2many('mos.paper.slip', 'book_id')
    used_count = fields.Integer(compute='_compute_counts')
    open_count = fields.Integer(compute='_compute_counts')

    @api.depends('slip_ids.state')
    def _compute_counts(self):
        for book in self:
            book.used_count = len(book.slip_ids.filtered(lambda s: s.state == 'entered'))
            book.open_count = len(book.slip_ids.filtered(lambda s: s.state == 'issued'))

    @api.constrains('first_no', 'last_no')
    def _check_range(self):
        for book in self:
            if book.first_no < 1 or book.last_no < book.first_no or book.last_no - book.first_no > 999:
                raise ValidationError(self.env._('A book holds between 1 and 1000 slips, numbered upward.'))
            clash = self.search_count([('site_id', '=', book.site_id.id), ('prefix', '=', book.prefix), ('id', '!=', book.id), ('first_no', '<=', book.last_no), ('last_no', '>=', book.first_no)])
            if clash:
                raise ValidationError(self.env._('Another book already uses part of that number range.'))

    @api.model_create_multi
    def create(self, vals_list):
        books = super().create(vals_list)
        for book in books:
            book.name = f'{book.prefix}{book.first_no}-{book.last_no}'
            self.env['mos.paper.slip'].sudo().create([{'book_id': book.id, 'number': n} for n in range(book.first_no, book.last_no + 1)])
        return books


class MosPaperSlip(models.Model):
    """One slip: written by hand at the gate, entered into the system afterwards with the times written on it (FR-GATE-024)."""

    _name = 'mos.paper.slip'
    _inherit = ['mos.audited']
    _description = 'MOS paper slip'
    _order = 'book_id, number'

    book_id = fields.Many2one('mos.paper.book', required=True, ondelete='cascade')
    site_id = fields.Many2one(related='book_id.site_id', store=True)
    number = fields.Integer(required=True)
    code = fields.Char(compute='_compute_code', store=True)
    state = fields.Selection([('issued', 'Issued, not entered'), ('entered', 'Entered'), ('void', 'Void')], default='issued', required=True, readonly=True)
    plate = fields.Char()
    driver_name = fields.Char()
    purpose = fields.Selection([('inbound_po', 'Inbound: PO delivery'), ('outbound_do', 'Outbound: customer delivery'), ('service', 'Service or contractor visit')], default='inbound_po')
    purchase_id = fields.Many2one('purchase.order')
    arrived_at = fields.Datetime()
    exited_at = fields.Datetime()
    gross_kg = fields.Float(digits=(12, 1))
    tare_kg = fields.Float(digits=(12, 1))
    void_reason = fields.Char(readonly=True)
    visit_id = fields.Many2one('mos.gate.visit', readonly=True)
    entered_by_id = fields.Many2one('res.users', readonly=True)
    entered_at = fields.Datetime(readonly=True)

    _number_uniq = models.Constraint('unique(book_id, number)', 'A slip number appears once in a book.')

    @api.depends('book_id.prefix', 'number')
    def _compute_code(self):
        for slip in self:
            slip.code = f'{slip.book_id.prefix or ""}{slip.number:04d}'

    def action_void(self, reason):
        for slip in self:
            if slip.state != 'issued':
                raise UserError(self.env._('Only a slip that has not been entered can be voided.'))
            if not reason:
                raise UserError(self.env._('A reason is required to void a slip.'))
            slip.write({'state': 'void', 'void_reason': reason})

    def action_enter(self):
        """Create the visit the slip describes, dated as written on the slip, and mark the slip entered."""
        for slip in self:
            if slip.state != 'issued':
                raise UserError(self.env._('This slip has already been entered or voided.'))
            if not slip.plate or not slip.arrived_at:
                raise UserError(self.env._('Write the plate and the arrival time from the slip.'))
            if slip.exited_at and slip.exited_at < slip.arrived_at:
                raise UserError(self.env._('The slip says the vehicle left before it arrived.'))
            vals = {
                'site_id': slip.site_id.id,
                'plate': slip.plate,
                'driver_name': slip.driver_name,
                'purpose': slip.purpose,
                'purchase_id': slip.purchase_id.id,
                'partner_id': slip.purchase_id.partner_id.id,
                'arrived_at': slip.arrived_at,
                'state': 'exited' if slip.exited_at else 'arrived',
                'exited_at': slip.exited_at,
                'from_paper': True,
                'reference_doc': slip.code,
            }
            visit = self.env['mos.gate.visit'].sudo().create(vals)
            if slip.gross_kg and slip.tare_kg:
                if slip.tare_kg >= slip.gross_kg and slip.purpose != 'outbound_do':
                    raise UserError(self.env._('The tare is not below the gross on the slip.'))
                user = self.env.user
                self.env['mos.weigh.ticket'].sudo().create(
                    {
                        'visit_id': visit.id,
                        'first_weight': slip.gross_kg if slip.purpose != 'outbound_do' else slip.tare_kg,
                        'second_weight': slip.tare_kg if slip.purpose != 'outbound_do' else slip.gross_kg,
                        'first_by_id': user.id,
                        'second_by_id': user.id,
                        'verified_by_id': user.id,
                        'provisional': True,
                        'state': 'posted',
                    }
                )
            slip.write({'state': 'entered', 'visit_id': visit.id, 'entered_by_id': self.env.uid, 'entered_at': fields.Datetime.now()})
            self.env['mos.audit.event'].record('paper_slip_entered', slip, f'{slip.code}: {visit.name}')
