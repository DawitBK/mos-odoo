from odoo import api, models


class ReportGrn(models.AbstractModel):
    """The goods receipt note and the supplier receipt print the gate visit that brought the goods, so one lookup serves both."""

    _name = 'report.mos_print.report_grn'
    _description = 'Goods receipt note'

    @api.model
    def _get_report_values(self, docids, data=None):
        pickings = self.env['stock.picking'].browse(docids)
        visits = {p.id: self.env['mos.gate.visit'].search([('grn_picking_id', '=', p.id)], limit=1) for p in pickings}
        return {'doc_ids': docids, 'doc_model': 'stock.picking', 'docs': pickings, 'visits': visits}
