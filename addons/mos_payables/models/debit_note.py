from odoo import api, fields, models


class MosDebitNote(models.Model):
    """What the supplier owes back: gap-free numbered, immutable once posted, and it reduces what is payable on the
    invoice (FR-AP-005)."""

    _name = 'mos.debit.note'
    _inherit = ['mos.posted']
    _description = 'MOS debit note'
    _order = 'id desc'
    _mos_final_states = ('posted',)

    name = fields.Char(readonly=True, copy=False, default='New')
    invoice_id = fields.Many2one('mos.supplier.invoice', required=True, ondelete='restrict')
    supplier_id = fields.Many2one('res.partner', required=True)
    amount = fields.Float(required=True)
    reason = fields.Char(required=True)
    date = fields.Date(default=fields.Date.context_today)
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.debit.note')
        return super().create(vals_list)
