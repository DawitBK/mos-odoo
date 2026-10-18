from odoo import api, fields, models
from odoo.tools import html_escape as esc

KINDS = [('arrival', 'Arrival acknowledged'), ('rejection', 'Rejection note'), ('receipt', 'Receipt with deductions'), ('payment', 'Payment advice')]


class MosSupplierNotice(models.Model):
    """Every notice owed to a supplier, with what became of it. A notice is queued as an e-mail; it leaves the server when an outgoing
    mail server is configured. A supplier with no address is recorded as such, so nobody believes they were told (FR-INB-009).

    Off until `mos_notify.supplier_notices` is set."""

    _name = 'mos.supplier.notice'
    _description = 'MOS supplier notice'
    _order = 'id desc'

    partner_id = fields.Many2one('res.partner', required=True, string='Supplier')
    kind = fields.Selection(KINDS, required=True)
    res_model = fields.Char()
    res_id = fields.Many2oneReference(model_field='res_model')
    subject = fields.Char()
    mail_id = fields.Many2one('mail.mail', readonly=True, ondelete='set null')
    state = fields.Selection([('queued', 'Queued'), ('sent', 'Sent'), ('failed', 'Failed'), ('no_email', 'Supplier has no e-mail address')], compute='_compute_state', store=True, readonly=False)

    @api.depends('mail_id.state')
    def _compute_state(self):
        for notice in self:
            if not notice.mail_id:
                notice.state = notice.state or 'no_email'
            else:
                notice.state = {'sent': 'sent', 'exception': 'failed', 'cancel': 'failed'}.get(notice.mail_id.state, 'queued')

    @api.model
    def enabled(self):
        return self.env['ir.config_parameter'].sudo().get_bool('mos_notify.supplier_notices', False)

    @api.model
    def notify(self, partner, kind, record, subject, body_html):
        """Queue a notice to `partner` about `record`. Does nothing while notices are off. Returns the notice (or an empty set)."""
        if not self.enabled() or not partner:
            return self.browse()
        partner = partner.sudo().commercial_partner_id if partner.sudo().commercial_partner_id else partner.sudo()
        email = (partner.email or '').strip()
        vals = {'partner_id': partner.id, 'kind': kind, 'res_model': record._name, 'res_id': record.id, 'subject': subject}
        if not email:
            vals['state'] = 'no_email'
            return self.sudo().create(vals)
        mail = self.env['mail.mail'].sudo().create({'subject': subject, 'email_to': email, 'body_html': body_html, 'auto_delete': False, 'model': record._name, 'res_id': record.id})
        vals['mail_id'] = mail.id
        return self.sudo().create(vals)

    @staticmethod
    def _html_table(rows):
        return '<table border="1" cellpadding="4" cellspacing="0">' + ''.join(f'<tr><td>{esc(a)}</td><td style="text-align:right">{esc(b)}</td></tr>' for a, b in rows) + '</table>'


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    @api.model_create_multi
    def create(self, vals_list):
        visits = super().create(vals_list)
        for visit in visits.filtered(lambda v: v.state == 'arrived'):
            visit._mos_notice_arrival()
        return visits

    def action_arrive(self):
        result = super().action_arrive()
        for visit in self:
            visit._mos_notice_arrival()
        return result

    def _mos_notice_arrival(self):
        self.ensure_one()
        if self.purpose != 'inbound_po' or not self.partner_id:
            return
        notice = self.env['mos.supplier.notice']
        order = self.sudo().purchase_id
        notice.notify(
            self.partner_id,
            'arrival',
            self,
            self.env._('Your delivery %(plate)s has arrived', plate=self.plate),
            f"<p>{esc(self.env._('Your vehicle %(plate)s arrived at %(site)s on %(when)s.', plate=self.plate, site=self.site_id.sudo().name, when=self.arrived_at))}</p>"
            + (f"<p>{esc(self.env._('Order: %s', order.name))}</p>" if order else ''),
        )

    def _mos_after_confirm(self):
        super()._mos_after_confirm()
        for visit in self:
            visit._mos_notice_receipt()

    def _mos_notice_receipt(self):
        self.ensure_one()
        insp = self.inspection_id
        order = self.sudo().purchase_id
        if not (insp and self.partner_id):
            return
        rows = [
            (self.env._('Order'), order.name or ''),
            (self.env._('Net weight (kg)'), f'{insp.net_weight:,.1f}'),
            (self.env._('Quality deduction (%)'), f'{insp.deduction_pct:.2f}'),
            (self.env._('Price adjustment (%)'), f'{insp.price_adjust_pct:.2f}'),
            (self.env._('Payable quantity (kg)'), f'{insp.payable_qty:,.1f}'),
        ]
        self.env['mos.supplier.notice'].notify(
            self.partner_id,
            'receipt',
            self,
            self.env._('Receipt for your delivery %(plate)s', plate=self.plate),
            f"<p>{esc(self.env._('Your delivery has been received. This is how the payable quantity was reached:'))}</p>" + self.env['mos.supplier.notice']._html_table(rows),
        )


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    def decide(self, decision, deduction_pct=0.0, note=''):
        result = super().decide(decision, deduction_pct, note)
        for insp in self:
            if insp.decision == 'reject':
                insp._mos_notice_rejection(note)
        return result

    def _mos_notice_rejection(self, note=''):
        self.ensure_one()
        visit = self.visit_id
        if not visit.partner_id:
            return
        rows = [(f'{r.line_id.name} (allowed {r.line_id.min_value:g} to {r.line_id.max_value:g})', f'{r.value:g}' + ('' if r.passed else ' FAILED')) for r in self.result_ids.filtered('valid')]
        self.env['mos.supplier.notice'].notify(
            visit.partner_id,
            'rejection',
            visit,
            self.env._('Your delivery %(plate)s was rejected', plate=visit.plate),
            f"<p>{esc(self.env._('Your delivery %(plate)s did not meet the specification and was rejected.', plate=visit.plate))}</p>"
            + self.env['mos.supplier.notice']._html_table(rows)
            + (f'<p>{esc(note)}</p>' if note else ''),
        )


class MosPayment(models.Model):
    _inherit = 'mos.payment'

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        for pay in payments:
            invoices = ', '.join(pay.voucher_id.sudo().line_ids.invoice_id.mapped('name'))
            rows = [
                (self.env._('Payment'), pay.name),
                (self.env._('Amount'), f'{pay.amount:,.2f}'),
                (self.env._('Method'), dict(pay._fields['method'].selection).get(pay.method, pay.method)),
                (self.env._('Date'), str(pay.date)),
            ]
            if pay.cheque_number:
                rows.append((self.env._('Cheque'), pay.cheque_number))
            if pay.reference:
                rows.append((self.env._('Reference'), pay.reference))
            if invoices:
                rows.append((self.env._('For invoices'), invoices))
            self.env['mos.supplier.notice'].notify(
                pay.supplier_id,
                'payment',
                pay,
                self.env._('Payment advice %(name)s', name=pay.name),
                f"<p>{esc(self.env._('We have paid you:'))}</p>" + self.env['mos.supplier.notice']._html_table(rows),
            )
        return payments
