from odoo import fields, models


class MosReason(models.Model):
    """Reason codes by domain (FR-MD-009): adjustment, scrap, gate rejection, re-weigh and so on."""

    _name = 'mos.reason'
    _description = 'MOS reason code'
    _order = 'domain, code'

    domain = fields.Selection(
        [
            ('adjustment', 'Stock adjustment'),
            ('scrap', 'Scrap'),
            ('status_change', 'Stock status change'),
            ('transit_loss', 'Transit loss'),
            ('count', 'Count difference'),
            ('gate_rejection', 'Gate rejection'),
            ('reweigh', 'Re-weigh'),
            ('quality', 'Quality'),
        ],
        required=True,
    )
    code = fields.Char(required=True)
    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(domain, code)', 'A reason code must be unique within its domain.')
