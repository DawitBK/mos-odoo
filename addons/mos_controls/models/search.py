from odoo import fields, models
from odoo.exceptions import AccessError

# model, fields searched, how to name a hit
TARGETS = [
    ('mos.gate.visit', ['name', 'plate', 'driver_name', 'transporter'], lambda r: f'{r.name}: {r.plate}'),
    ('mos.weigh.ticket', ['name'], lambda r: f'{r.name} ({r.visit_id.plate})'),
    ('purchase.order', ['name', 'partner_ref', 'partner_id.name'], lambda r: f'{r.name}: {r.partner_id.display_name}'),
    ('mos.supplier.invoice', ['name', 'supplier_ref', 'supplier_id.name'], lambda r: f'{r.name}: {r.supplier_ref}'),
    ('mos.payment.voucher', ['name'], lambda r: r.name),
    ('mos.customer.invoice', ['name'], lambda r: r.name),
    ('res.partner', ['name', 'ref'], lambda r: r.display_name),
    ('mos.lab.inspection', ['name'], lambda r: r.name),
    ('mos.purchase.request', ['name', 'reason'], lambda r: f'{r.name}: {r.reason[:60]}'),
    ('mos.gate.pass', ['name', 'carrier'], lambda r: f'{r.name}: {r.carrier}'),
    ('mos.stock.adjustment', ['name'], lambda r: r.name),
    ('mos.approval.request', ['name', 'doc_name'], lambda r: f'{r.name}: {r.doc_name}'),
    ('mos.exception', ['name', 'summary'], lambda r: f'{r.name}: {r.summary}'),
]


class MosSearch(models.TransientModel):
    """One box to find a truck, ticket, order, invoice, supplier or approval, within what the person is allowed to see (FR-PLT-080)."""

    _name = 'mos.search'
    _description = 'MOS global search'

    query = fields.Char(string='Look for', required=True)
    result_ids = fields.One2many('mos.search.result', 'search_id', string='Found')
    count = fields.Integer(readonly=True)

    def action_search(self):
        self.ensure_one()
        text = (self.query or '').strip()
        self.result_ids.unlink()
        if len(text) < 2:
            return self._reopen()
        rows = []
        for model, fields_, label in TARGETS:
            if model not in self.env:
                continue
            Model = self.env[model]
            try:
                domain = ['|'] * (len(fields_) - 1) + [(f, 'ilike', text) for f in fields_]
                hits = Model.search(domain, limit=8)
                for rec in hits:
                    rows.append({'search_id': self.id, 'res_model': model, 'res_id': rec.id, 'label': label(rec), 'kind': Model._description or model})
            except AccessError:
                continue
        self.env['mos.search.result'].create(rows)
        self.count = len(rows)
        return self._reopen()

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}


class MosSearchResult(models.TransientModel):
    _name = 'mos.search.result'
    _description = 'MOS global search hit'

    search_id = fields.Many2one('mos.search', required=True, ondelete='cascade')
    kind = fields.Char()
    label = fields.Char()
    res_model = fields.Char()
    res_id = fields.Integer()

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'view_mode': 'form', 'target': 'current'}
