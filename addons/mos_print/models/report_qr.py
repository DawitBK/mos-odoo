import hashlib

from odoo import models


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _get_rendering_context(self, report, docids, data):
        context = super()._get_rendering_context(report, docids, data)
        if (report.report_name or '').startswith('mos_print.'):
            context['mos_qr'] = self._mos_qr_value
        return context

    def _mos_qr_value(self, doc):
        """What the QR code on a printed document says: the document's model, id and number, and a short hash that only this database
        can produce, so a scan can tell a genuine print from a typed-up copy (TDD 17.6)."""
        secret = self.env['ir.config_parameter'].sudo().get_str('database.secret')
        name = doc.display_name or ''
        digest = hashlib.sha256(f'{secret}|{doc._name}|{doc.id}|{name}'.encode()).hexdigest()[:10]
        return f'MOS|{doc._name}|{doc.id}|{name}|{digest}'
