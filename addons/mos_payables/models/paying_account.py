from odoo import api, fields, models
from odoo.exceptions import UserError


class MosPayingAccount(models.Model):
    """A company bank account, cash box or mobile-money wallet that payments leave from (FR-AP-015)."""

    _name = 'mos.paying.account'
    _inherit = ['mos.audited']
    _description = 'MOS paying account'
    _order = 'name'

    name = fields.Char(required=True)
    kind = fields.Selection([('bank', 'Bank account'), ('cash', 'Cash box'), ('wallet', 'Mobile-money wallet')], required=True, default='bank')
    site_id = fields.Many2one('mos.site')
    active = fields.Boolean(default=True)
    paid_total = fields.Float(compute='_compute_paid', string='Paid out')
    book_ids = fields.One2many('mos.cheque.book', 'account_id', string='Cheque books')

    def _compute_paid(self):
        groups = self.env['mos.payment'].sudo()._read_group([('account_id', 'in', self.ids)], ['account_id'], ['amount:sum'])
        totals = {acc.id: amt for acc, amt in groups}
        for acc in self:
            acc.paid_total = totals.get(acc.id, 0.0)


class MosChequeBook(models.Model):
    """A book of cheques registered by number range. Every leaf is accounted for: unused, issued to a payment, or
    voided with a reason; issuing out of order is allowed but shows as a gap (FR-AP-012, 016)."""

    _name = 'mos.cheque.book'
    _inherit = ['mos.audited']
    _description = 'MOS cheque book'

    name = fields.Char(compute='_compute_name', store=True)
    account_id = fields.Many2one('mos.paying.account', required=True, ondelete='restrict')
    prefix = fields.Char(default='')
    first_no = fields.Integer(required=True)
    last_no = fields.Integer(required=True)
    leaf_ids = fields.One2many('mos.cheque.leaf', 'book_id')
    unused = fields.Integer(compute='_compute_counts')
    gaps = fields.Char(compute='_compute_counts', string='Skipped numbers')

    _range_valid = models.Constraint('CHECK(last_no >= first_no)', 'The last cheque number is below the first.')

    @api.depends('prefix', 'first_no', 'last_no', 'account_id.name')
    def _compute_name(self):
        for book in self:
            book.name = f'{book.account_id.name or "?"} {book.prefix or ""}{book.first_no}-{book.last_no}'

    @api.model_create_multi
    def create(self, vals_list):
        books = super().create(vals_list)
        for book in books:
            # No two books of one account may share numbers.
            clash = self.search_count([('account_id', '=', book.account_id.id), ('prefix', '=', book.prefix or ''), ('id', '!=', book.id), ('first_no', '<=', book.last_no), ('last_no', '>=', book.first_no)])
            if clash:
                raise UserError(self.env._('Cheque numbers must be unique per account: this range overlaps another book.'))
            self.env['mos.cheque.leaf'].sudo().create([{'book_id': book.id, 'number': n} for n in range(book.first_no, book.last_no + 1)])
        return books

    def _compute_counts(self):
        for book in self:
            leaves = book.leaf_ids
            book.unused = len(leaves.filtered(lambda l: l.state == 'unused'))
            used = sorted(leaves.filtered(lambda l: l.state != 'unused').mapped('number'))
            skipped = [str(l.number) for l in leaves.filtered(lambda l: l.state == 'unused' and used and l.number < used[-1])]
            book.gaps = ', '.join(skipped[:20]) + (' …' if len(skipped) > 20 else '')

    def next_leaf(self):
        self.ensure_one()
        leaf = self.leaf_ids.filtered(lambda l: l.state == 'unused').sorted('number')[:1]
        if not leaf:
            raise UserError(self.env._('The cheque book %s has no unused cheques left.', self.name))
        return leaf


class MosChequeLeaf(models.Model):
    _name = 'mos.cheque.leaf'
    _description = 'MOS cheque'
    _order = 'book_id, number'

    book_id = fields.Many2one('mos.cheque.book', required=True, ondelete='restrict', index=True)
    number = fields.Integer(required=True)
    full_number = fields.Char(compute='_compute_full', store=True)
    state = fields.Selection([('unused', 'Unused'), ('issued', 'Issued'), ('void', 'Voided')], default='unused', required=True)
    void_reason = fields.Char()
    payment_id = fields.Many2one('mos.payment', readonly=True)

    _number_uniq = models.Constraint('unique(book_id, number)', 'A cheque number appears once in a book.')

    @api.depends('book_id.prefix', 'number')
    def _compute_full(self):
        for leaf in self:
            leaf.full_number = f'{leaf.book_id.prefix or ""}{leaf.number:06d}'

    def action_void(self, reason):
        for leaf in self:
            if leaf.state != 'unused':
                raise UserError(self.env._('Only an unused cheque can be voided; an issued one is reversed through its payment.'))
            if not reason:
                raise UserError(self.env._('A reason is required to void a cheque.'))
            leaf.write({'state': 'void', 'void_reason': reason})
