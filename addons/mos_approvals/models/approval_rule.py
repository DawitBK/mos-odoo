from odoo import api, fields, models
from odoo.exceptions import ValidationError


class MosApprovalRule(models.Model):
    """One row of the approval matrix: for this kind of document, in this amount band (and site), this role
    approves, in this order. The matrix is data, never code (FR-PLT-023). Rows that match one document form its chain,
    stage by stage in ``sequence`` order."""

    _name = 'mos.approval.rule'
    _inherit = ['mos.audited']
    _description = 'MOS approval matrix row'
    _order = 'doc_type, sequence, id'

    doc_type = fields.Selection([('purchase.order', 'Purchase order')], required=True)
    sequence = fields.Integer(string='Stage', default=10, help='Stages run in this order, one after the other.')
    amount_from = fields.Float(default=0.0, help='From this amount, inclusive.')
    amount_to = fields.Float(default=0.0, help='Up to this amount, inclusive. Zero means no upper limit.')
    site_id = fields.Many2one('mos.site', help='Leave empty for every site.')
    group_id = fields.Many2one('res.groups', string='Approving role', required=True)
    fallback_group_id = fields.Many2one('res.groups', string='Escalates to')
    sla_hours = fields.Integer(string='Respond within (hours)', default=24)
    allow_bulk = fields.Boolean(string='Bulk approval allowed', default=False)
    active = fields.Boolean(default=True)

    @api.constrains('amount_from', 'amount_to')
    def _check_band(self):
        for rule in self:
            if rule.amount_from < 0 or (rule.amount_to and rule.amount_to < rule.amount_from):
                raise ValidationError(self.env._('The amount band is not valid: the upper limit is below the lower one.'))

    @api.model
    def matching(self, doc_type, amount, site=None):
        """The rows that apply to a document of this kind, amount and site, in stage order."""
        rules = self.search([('doc_type', '=', doc_type)])
        picked = rules.filtered(
            lambda r: r.amount_from <= amount and (not r.amount_to or amount <= r.amount_to) and (not r.site_id or (site and r.site_id == site))
        )
        return picked.sorted(lambda r: (r.sequence, r.id))
