from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError


class MosPvDraft(models.Model):
    """The payment that a confirmed receipt sets in motion (FR-INB-003).

    One row per receipt, never two (the visit is unique, so duplicate events change nothing). It waits for the supplier's
    invoice; when a matched payable invoice for the order arrives, a voucher is drafted for the payables clerk, and in
    auto-submit mode a voucher whose invoice matched within tolerance goes straight to approval.
    """

    _name = 'mos.pv.draft'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS payment drafted from a receipt'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    visit_id = fields.Many2one('mos.gate.visit', required=True, ondelete='restrict')
    purchase_id = fields.Many2one('purchase.order', required=True)
    supplier_id = fields.Many2one(related='purchase_id.partner_id', store=True)
    product_id = fields.Many2one('product.product', readonly=True)
    received_qty = fields.Float(string='Payable quantity (kg)', digits=(14, 1), readonly=True)
    expected_gross = fields.Float(string='Expected before tax', readonly=True)
    invoice_id = fields.Many2one('mos.supplier.invoice', readonly=True)
    voucher_id = fields.Many2one('mos.payment.voucher', readonly=True)
    state = fields.Selection(
        [('awaiting_invoice', 'Awaiting invoice'), ('drafted', 'Voucher drafted'), ('submitted', 'Voucher submitted'), ('reversed', 'Receipt reversed')],
        default='awaiting_invoice',
        required=True,
        readonly=True,
        tracking=True,
    )

    _visit_uniq = models.Constraint('unique(visit_id)', 'A receipt starts only one payment.')

    @api.model
    def for_visit(self, visit):
        """The payment draft of a confirmed receipt: created once, found again on every repeat."""
        existing = self.sudo().search([('visit_id', '=', visit.id)], limit=1)
        if existing:
            return existing
        insp = visit.inspection_id
        if insp and insp.decision == 'reject':
            return self.browse()
        qty = insp.payable_qty if insp else (visit.received_qty or visit.net_weight)
        lines = visit.sudo().purchase_id.order_line.filtered(lambda line: line.product_id == (insp.product_id if insp else visit._po_product()))
        price = lines[:1].price_unit
        draft = self.sudo().create(
            {
                'name': self.env['ir.sequence'].next_by_code('mos.pv.draft'),
                'visit_id': visit.id,
                'purchase_id': visit.sudo().purchase_id.id,
                'product_id': lines[:1].product_id.id,
                'received_qty': qty,
                'expected_gross': round(qty * price, 2),
            }
        )
        visit.sudo().write({'pv_draft_id': draft.id})
        partner = draft.supplier_id
        if not partner.sudo().mos_bank_account_enc:
            # Missing finance setup never blocks receiving; it becomes a task (FR-INB-006).
            self.env['mos.exception'].raise_for('pv_setup', draft, self.env._('%s has no payment account on file', partner.display_name), severity='medium', due_days=3)
        draft._try_link()
        return draft

    def _try_link(self):
        """Find the matched payable invoice for the order and draft the voucher from it."""
        for draft in self.filtered(lambda d: d.state == 'awaiting_invoice'):
            invoice = self.env['mos.supplier.invoice'].sudo().search(
                [('purchase_id', '=', draft.purchase_id.id), ('supplier_id', '=', draft.supplier_id.id), ('state', '=', 'payable')],
                order='id',
            ).filtered(lambda i: not i.voucher_line_ids)[:1]
            if not invoice or self.sudo().search_count([('invoice_id', '=', invoice.id)]):
                continue
            voucher = self.env['mos.payment.voucher'].sudo().create({'supplier_id': draft.supplier_id.id, 'line_ids': [(0, 0, {'invoice_id': invoice.id})]})
            draft.write({'invoice_id': invoice.id, 'voucher_id': voucher.id, 'state': 'drafted'})
            if self.env['mos.flow.setting'].mode('pv') == 'auto_submit' and invoice.match_state == 'matched':
                voucher.with_user(SUPERUSER_ID).action_submit()
                draft.write({'state': 'submitted'})
            self.env['mos.audit.event'].record('pv_drafted', draft, f'{draft.name}: {voucher.name} from {invoice.name}')

    @api.model
    def _cron_link(self):
        self.search([('state', '=', 'awaiting_invoice')])._try_link()

    def action_reverse(self):
        """The receipt was reversed before its payment went anywhere."""
        for draft in self:
            if draft.state == 'submitted':
                raise UserError(self.env._('The voucher is already submitted; cancel it there.'))
            if draft.voucher_id and draft.voucher_id.state == 'draft':
                draft.voucher_id.sudo().action_cancel()
            draft.write({'state': 'reversed'})


class MosSupplierInvoice(models.Model):
    _inherit = 'mos.supplier.invoice'

    def action_match(self):
        result = super().action_match()
        for invoice in self.filtered(lambda i: i.state == 'payable'):
            self.env['mos.pv.draft'].sudo().search([('purchase_id', '=', invoice.purchase_id.id), ('state', '=', 'awaiting_invoice')])._try_link()
        return result
