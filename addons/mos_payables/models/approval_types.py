from odoo import fields, models

TYPES = [('mos.supplier.invoice', 'Supplier invoice (match exception)'), ('mos.payment.voucher', 'Payment voucher')]


class MosApprovalRule(models.Model):
    _inherit = 'mos.approval.rule'

    doc_type = fields.Selection(selection_add=TYPES, ondelete={t[0]: 'cascade' for t in TYPES})


class MosApprovalDelegation(models.Model):
    _inherit = 'mos.approval.delegation'

    doc_type = fields.Selection(selection_add=TYPES, ondelete={t[0]: 'set null' for t in TYPES})
