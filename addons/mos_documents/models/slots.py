from odoo import api, fields, models


class MosDocumentSlots(models.AbstractModel):
    """Gives a document its attached files, typed and verified, shown in a panel at the foot of its form."""

    _name = 'mos.document.slots'
    _description = 'Document with attached files'

    mos_attachment_ids = fields.One2many(
        'mos.attachment', 'res_id', string='Attached documents', domain=lambda self: [('res_model', '=', self._name)], copy=False
    )
    mos_attachment_count = fields.Integer(string='Attached files', compute='_compute_attachment_count')
    mos_unverified_count = fields.Integer(string='Files not verified', compute='_compute_attachment_count')

    @api.depends('mos_attachment_ids.verify_state')
    def _compute_attachment_count(self):
        for doc in self:
            doc.mos_attachment_count = len(doc.mos_attachment_ids)
            doc.mos_unverified_count = len(doc.mos_attachment_ids.filtered(lambda a: a.verify_state != 'verified'))

    def _mos_has_verified(self, kind):
        """Whether a file of this kind is attached and verified, for the rules that need a document before a step."""
        self.ensure_one()
        return bool(self.sudo().mos_attachment_ids.filtered(lambda a: a.kind == kind and a.verify_state == 'verified'))

    def _mos_has_attached(self, kind):
        self.ensure_one()
        return bool(self.sudo().mos_attachment_ids.filtered(lambda a: a.kind == kind))


class PurchaseOrder(models.Model):
    _inherit = ['purchase.order', 'mos.document.slots']


class MosPurchaseRequest(models.Model):
    _inherit = ['mos.purchase.request', 'mos.document.slots']


class MosGateVisit(models.Model):
    _inherit = ['mos.gate.visit', 'mos.document.slots']


class MosPaymentVoucher(models.Model):
    _inherit = ['mos.payment.voucher', 'mos.document.slots']


class MosSupplierInvoice(models.Model):
    _inherit = ['mos.supplier.invoice', 'mos.document.slots']
