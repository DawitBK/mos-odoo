import base64
import csv
import hashlib
import io

from odoo import api, fields, models
from odoo.exceptions import UserError

KEYS = [
    ('inventory', 'Inventory or purchases (debit on a supplier invoice)'),
    ('input_vat', 'Input VAT'),
    ('ap_control', 'Accounts payable control'),
    ('wht_payable', 'Withholding tax payable'),
    ('bank', 'Bank (transfers, cheques, wallets)'),
    ('cash', 'Cash box'),
    ('ar_control', 'Accounts receivable control'),
    ('sales', 'Sales'),
    ('output_vat', 'Output VAT'),
]


class MosAccountMap(models.Model):
    """Which account of the external books each kind of posting goes to (FR-AP-010)."""

    _name = 'mos.account.map'
    _inherit = ['mos.audited']
    _description = 'MOS external account mapping'
    _rec_name = 'key'

    key = fields.Selection(KEYS, required=True)
    code = fields.Char(string='Account code', required=True)
    name = fields.Char(string='Account name')

    _key_uniq = models.Constraint('unique(key)', 'This posting kind is mapped once.')

    @api.model
    def codes(self):
        return {row.key: row.code for row in self.sudo().search([])}


class MosJournalBatch(models.Model):
    """One export of postings for the accountants, with its file, hash and the documents it contains. A document is exported
    once; the batch can be downloaded again but never regenerated differently (FR-AP-010)."""

    _name = 'mos.journal.batch'
    _inherit = ['mos.posted']
    _description = 'MOS journal export'
    _order = 'id desc'
    _mos_final_states = ('exported',)

    name = fields.Char(readonly=True, copy=False, default='New')
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection([('exported', 'Exported')], default='exported', readonly=True)
    file = fields.Binary(attachment=True, readonly=True)
    filename = fields.Char(readonly=True)
    sha256 = fields.Char(readonly=True)
    entries = fields.Integer(readonly=True)
    total_debit = fields.Float(readonly=True)
    exported_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    line_ids = fields.One2many('mos.journal.line', 'batch_id', readonly=True)
    doc_ids = fields.One2many('mos.journal.doc', 'batch_id', readonly=True)

    @api.model
    def export(self, date_from, date_to):
        if date_to < date_from:
            raise UserError(self.env._('The end date is before the start date.'))
        user = self.env.user
        if not (user.has_group('mos_approvals.group_mos_finance_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a finance manager can export the journals.'))
        codes = self.env['mos.account.map'].codes()
        rows, docs = self._collect(date_from, date_to, codes)
        missing = sorted({r['key'] for r in rows if r['key'] not in codes})
        if missing:
            raise UserError(self.env._('Map these posting kinds to accounts first: %s.', ', '.join(missing)))
        by_doc = {}
        for r in rows:
            by_doc.setdefault((r['model'], r['id']), [0.0, 0.0])
            by_doc[(r['model'], r['id'])][0] += r['debit']
            by_doc[(r['model'], r['id'])][1] += r['credit']
        for (model, rid), (debit, credit) in by_doc.items():
            if abs(debit - credit) > 0.01:
                raise UserError(self.env._('The entry for %(model)s %(id)s does not balance (%(d).2f against %(c).2f).', model=model, id=rid, d=debit, c=credit))
        if not rows:
            raise UserError(self.env._('There is nothing new to export for that period.'))
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(['Date', 'Journal', 'Reference', 'Account', 'Partner', 'Description', 'Debit', 'Credit'])
        for r in rows:
            writer.writerow([r['date'], r['journal'], r['reference'], codes[r['key']], r['partner'], r['description'], f"{r['debit']:.2f}", f"{r['credit']:.2f}"])
        content = buffer.getvalue().encode('utf-8')
        batch = self.create(
            {
                'name': self.env['ir.sequence'].next_by_code('mos.journal.batch'),
                'date_from': date_from,
                'date_to': date_to,
                'file': base64.b64encode(content).decode(),
                'filename': f'mos-journal-{date_from}-{date_to}.csv',
                'sha256': hashlib.sha256(content).hexdigest(),
                'entries': len(by_doc),
                'total_debit': round(sum(r['debit'] for r in rows), 2),
                'line_ids': [(0, 0, {'date': r['date'], 'journal': r['journal'], 'reference': r['reference'], 'account_code': codes[r['key']], 'partner': r['partner'], 'description': r['description'], 'debit': r['debit'], 'credit': r['credit']}) for r in rows],
            }
        )
        self.env['mos.journal.doc'].sudo().create([{'batch_id': batch.id, 'res_model': m, 'res_id': i} for m, i in docs])
        self.env['mos.audit.event'].record('journal_exported', batch, f'{batch.name}: {len(by_doc)} entries, sha256 {batch.sha256[:12]}')
        return batch

    @api.model
    def _collect(self, date_from, date_to, codes):
        """Every posting in the period that has not been exported, as balanced debit and credit rows."""
        sent = {(d.res_model, d.res_id) for d in self.env['mos.journal.doc'].sudo().search([])}
        rows, docs = [], []

        def add(model, rec, journal, date, key, debit, credit, partner, description, reference=None):
            if debit or credit:
                rows.append({'model': model, 'id': rec.id, 'journal': journal, 'date': date, 'reference': reference or rec.name, 'key': key, 'debit': round(debit, 2), 'credit': round(credit, 2), 'partner': partner.display_name or '', 'description': description})

        def take(model, domain):
            return self.env[model].sudo().search(domain + [('id', 'not in', [i for m, i in sent if m == model])], order='id')

        for inv in take('mos.supplier.invoice', [('state', 'not in', ('draft', 'cancelled')), ('invoice_date', '>=', date_from), ('invoice_date', '<=', date_to)]):
            docs.append(('mos.supplier.invoice', inv.id))
            add('mos.supplier.invoice', inv, 'PURCH', inv.invoice_date, 'inventory', inv.net_amount, 0.0, inv.supplier_id, f'Purchase {inv.supplier_ref}')
            add('mos.supplier.invoice', inv, 'PURCH', inv.invoice_date, 'input_vat', inv.vat_amount, 0.0, inv.supplier_id, f'VAT {inv.supplier_ref}')
            add('mos.supplier.invoice', inv, 'PURCH', inv.invoice_date, 'ap_control', 0.0, inv.gross_amount - inv.wht_amount, inv.supplier_id, f'Payable {inv.supplier_ref}')
            add('mos.supplier.invoice', inv, 'PURCH', inv.invoice_date, 'wht_payable', 0.0, inv.wht_amount, inv.supplier_id, f'Withholding {inv.supplier_ref}')
        for note in take('mos.debit.note', [('date', '>=', date_from), ('date', '<=', date_to)]):
            docs.append(('mos.debit.note', note.id))
            vat_pct = note.invoice_id.vat_pct
            net = round(note.amount / (1 + vat_pct / 100.0), 2)
            vat = round(note.amount - net, 2)
            add('mos.debit.note', note, 'PURCH', note.date, 'ap_control', note.amount, 0.0, note.supplier_id, f'Debit note {note.reason}')
            add('mos.debit.note', note, 'PURCH', note.date, 'inventory', 0.0, net, note.supplier_id, f'Debit note {note.reason}')
            add('mos.debit.note', note, 'PURCH', note.date, 'input_vat', 0.0, vat, note.supplier_id, f'Debit note VAT')
        for pay in take('mos.payment', [('date', '>=', date_from), ('date', '<=', date_to)]):
            docs.append(('mos.payment', pay.id))
            add('mos.payment', pay, 'BANK', pay.date, 'ap_control', pay.amount, 0.0, pay.supplier_id, f'Payment {pay.reference or pay.method}')
            add('mos.payment', pay, 'BANK', pay.date, 'cash' if pay.method == 'cash' else 'bank', 0.0, pay.amount, pay.supplier_id, f'Payment {pay.reference or pay.method}')
        for inv in take('mos.customer.invoice', [('date', '>=', date_from), ('date', '<=', date_to)]):
            docs.append(('mos.customer.invoice', inv.id))
            add('mos.customer.invoice', inv, 'SALES', inv.date, 'ar_control', inv.gross_amount, 0.0, inv.customer_id, f'Invoice {inv.name}')
            add('mos.customer.invoice', inv, 'SALES', inv.date, 'sales', 0.0, inv.net_amount, inv.customer_id, f'Invoice {inv.name}')
            add('mos.customer.invoice', inv, 'SALES', inv.date, 'output_vat', 0.0, inv.vat_amount, inv.customer_id, f'Invoice {inv.name}')
        for rec in take('mos.receipt', [('date', '>=', date_from), ('date', '<=', date_to)]):
            docs.append(('mos.receipt', rec.id))
            add('mos.receipt', rec, 'BANK', rec.date, 'cash' if rec.method == 'cash' else 'bank', rec.amount, 0.0, rec.customer_id, f'Receipt {rec.reference or rec.method}')
            add('mos.receipt', rec, 'BANK', rec.date, 'ar_control', 0.0, rec.amount, rec.customer_id, f'Receipt {rec.reference or rec.method}')
        if 'mos.credit.note' in self.env:
            for note in take('mos.credit.note', [('state', '=', 'posted'), ('date', '>=', date_from), ('date', '<=', date_to)]):
                docs.append(('mos.credit.note', note.id))
                vat_pct = note.invoice_id.vat_pct if note.invoice_id else 15.0
                net = round(note.amount / (1 + vat_pct / 100.0), 2)
                add('mos.credit.note', note, 'SALES', note.date, 'sales', net, 0.0, note.customer_id, f'Credit note {note.reason_id.name}')
                add('mos.credit.note', note, 'SALES', note.date, 'output_vat', round(note.amount - net, 2), 0.0, note.customer_id, 'Credit note VAT')
                add('mos.credit.note', note, 'SALES', note.date, 'ar_control', 0.0, note.amount, note.customer_id, f'Credit note {note.reason_id.name}')
        if 'mos.payment.reversal' in self.env:
            for rev in take('mos.payment.reversal', []):
                pay = rev.payment_id
                if not (date_from <= rev.create_date.date() <= date_to):
                    continue
                docs.append(('mos.payment.reversal', rev.id))
                add('mos.payment.reversal', rev, 'BANK', rev.create_date.date(), 'cash' if pay.method == 'cash' else 'bank', pay.amount, 0.0, pay.supplier_id, f'Reversal of {pay.name}')
                add('mos.payment.reversal', rev, 'BANK', rev.create_date.date(), 'ap_control', 0.0, pay.amount, pay.supplier_id, f'Reversal of {pay.name}')
        return rows, docs


class MosJournalLine(models.Model):
    _name = 'mos.journal.line'
    _description = 'MOS journal export line'
    _order = 'id'

    batch_id = fields.Many2one('mos.journal.batch', required=True, ondelete='restrict')
    date = fields.Date()
    journal = fields.Char()
    reference = fields.Char()
    account_code = fields.Char()
    partner = fields.Char()
    description = fields.Char()
    debit = fields.Float()
    credit = fields.Float()


class MosJournalDoc(models.Model):
    """A document that has been exported, so it is never exported twice."""

    _name = 'mos.journal.doc'
    _description = 'MOS exported document'

    batch_id = fields.Many2one('mos.journal.batch', required=True, ondelete='restrict')
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)

    _doc_once = models.Constraint('unique(res_model, res_id)', 'A document is exported once.')


class MosJournalExport(models.TransientModel):
    _name = 'mos.journal.export'
    _description = 'MOS journal export dialog'

    date_from = fields.Date(required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)

    def action_export(self):
        self.ensure_one()
        batch = self.env['mos.journal.batch'].export(self.date_from, self.date_to)
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.journal.batch', 'res_id': batch.id, 'view_mode': 'form', 'target': 'current'}
