from odoo import fields, models
from odoo.exceptions import UserError

KINDS = [
    ('supplier_invoice', "Supplier's invoice"),
    ('tax_document', 'Tax document'),
    ('delivery_note', 'Delivery note'),
    ('wht_certificate', 'Withholding certificate'),
    ('deposit_slip', 'Deposit slip'),
    ('specification', 'Specification'),
]


class MosAttachment(models.Model):
    """A document slot: the file says what kind of document it is, and a second person verifies it (FR-PLT-044)."""

    _inherit = 'mos.attachment'

    kind = fields.Selection(selection_add=KINDS, ondelete={code: 'set default' for code, _label in KINDS})
    verify_state = fields.Selection([('unverified', 'Not verified'), ('verified', 'Verified')], default='unverified', readonly=True, required=True)
    verified_by_id = fields.Many2one('res.users', readonly=True)
    verified_at = fields.Datetime(readonly=True)

    def action_verify(self):
        """Somebody other than the person who attached the file confirms it is the right document and is readable."""
        for att in self:
            if att.verify_state == 'verified':
                continue
            if att.uploaded_by_id == self.env.user:
                raise UserError(self.env._('A document is verified by somebody other than the person who attached it.'))
            att.sudo().write({'verify_state': 'verified', 'verified_by_id': self.env.uid, 'verified_at': fields.Datetime.now()})
            self.env['mos.audit.event'].record('document_verified', att, f'{att.name} ({att.kind}) on {att.res_model} {att.res_id}')
        return True
