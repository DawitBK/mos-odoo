from odoo import fields, models


class MosGateVisit(models.Model):
    """Outbound deliveries can need a certificate of analysis before the vehicle leaves (FR-QC-021)."""

    _inherit = 'mos.gate.visit'

    coa_ids = fields.One2many('mos.lab.coa', 'visit_id', string='Certificates of analysis')

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        required = self.env['ir.config_parameter'].sudo().get_bool('mos_lab.require_coa')
        if required and self.purpose == 'outbound_do' and not self.coa_ids.filtered(lambda c: c.state == 'issued'):
            problems.append(self.env._('no certificate of analysis has been issued for this delivery'))
        return problems
