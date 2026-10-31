from odoo import fields, models


class MosGatePassLine(models.Model):
    """A stock item on a final pass can leave on a posted goods issue (a scrap sale, a gift) as well as on a delivery (FR-GATE-019)."""

    _inherit = 'mos.gate.pass.line'

    inventory_issue_id = fields.Many2one('mos.stock.issue', string='Goods issue', domain="[('state', '=', 'issued')]")

    def _inventory_posted(self):
        self.ensure_one()
        return super()._inventory_posted() or self.inventory_issue_id.state == 'issued'
