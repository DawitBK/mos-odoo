from odoo import fields, models

TYPES = [('sale.order', 'Sales order')]


class MosApprovalRule(models.Model):
    _inherit = 'mos.approval.rule'

    doc_type = fields.Selection(selection_add=TYPES, ondelete={'sale.order': 'cascade'})


class MosApprovalDelegation(models.Model):
    _inherit = 'mos.approval.delegation'

    doc_type = fields.Selection(selection_add=TYPES, ondelete={'sale.order': 'set null'})
