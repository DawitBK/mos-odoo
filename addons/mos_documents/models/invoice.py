from odoo import api, fields, models
from odoo.exceptions import UserError


class MosSupplierInvoiceApproval(models.Model):
    """Optional approval of every supplier invoice, not only the ones with a match variance (FR-AP-020), and an optional rule that the
    supplier's invoice must be attached and verified before the invoice is matched.

    Off by default: ``mos_payables.approve_every_invoice`` switches the approval on, ``mos_documents.require_verified_invoice`` the
    document rule. With the approval on, a matched invoice waits as a draft until its approval chain accepts it (the approval status
    on the form carries the client's SUBMITTED and APPROVED), and only then becomes payable."""

    _inherit = 'mos.supplier.invoice'

    approval_gate = fields.Boolean(compute='_compute_approval_gate', help='A matched invoice that still has to be sent for approval.')

    def _approve_every_invoice(self):
        return self.env['ir.config_parameter'].sudo().get_bool('mos_payables.approve_every_invoice')

    def _mos_approval_amount(self):
        self.ensure_one()
        if self._approve_every_invoice():
            return self.gross_amount
        return super()._mos_approval_amount()

    def _mos_approval_required(self):
        self.ensure_one()
        if self._approve_every_invoice():
            return bool(self.env['mos.approval.rule'].sudo().matching(self._mos_doc_type(), self._mos_approval_amount(), self._mos_approval_site()))
        return super()._mos_approval_required()

    @api.depends('match_state', 'state', 'mos_approval_state')
    def _compute_approval_gate(self):
        for inv in self:
            inv.approval_gate = bool(
                inv._approve_every_invoice()
                and inv.match_state == 'matched'
                and inv.state == 'draft'
                and inv.mos_approval_state in ('none', 'returned', 'rejected', 'cancelled')
                and inv._mos_approval_required()
            )

    def action_match(self):
        if self.env['ir.config_parameter'].sudo().get_bool('mos_documents.require_verified_invoice'):
            for inv in self:
                if not inv._mos_has_verified('supplier_invoice'):
                    raise UserError(self.env._("Attach the supplier's invoice and have somebody else verify it before matching %s.", inv.display_name))
        result = super().action_match()
        if self._approve_every_invoice():
            for inv in self:
                if inv.match_state == 'matched' and inv.state == 'payable' and inv.mos_approval_state != 'approved' and inv._mos_approval_required():
                    inv.sudo().write({'state': 'draft'})
        return result

    def _mos_approval_done(self, request):
        super()._mos_approval_done(request)
        for inv in self:
            if inv.match_state == 'matched' and inv.state == 'draft':
                inv.sudo().write({'state': 'payable'})
