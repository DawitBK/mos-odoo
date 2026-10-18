from odoo import api, fields, models
from odoo.exceptions import UserError


class MosException(models.Model):
    _inherit = 'mos.exception'

    kind = fields.Selection(selection_add=[('receipt_exception', 'Receipt failed'), ('pv_setup', 'Payment set-up missing')], ondelete={'receipt_exception': 'cascade', 'pv_setup': 'cascade'})


class MosFlowSetting(models.Model):
    """The automation modes of this installation (FR-INB-010). Changing one is audited and touches only what happens afterwards."""

    _name = 'mos.flow.setting'
    _inherit = ['mos.audited']
    _description = 'MOS receiving automation modes'

    name = fields.Char(default='Receiving automation', readonly=True)
    grn_mode = fields.Selection(
        [('confirm', 'The storekeeper confirms each receipt'), ('automatic', 'Post the receipt as soon as it is prepared')],
        string='Goods receipt',
        default='confirm',
        required=True,
    )
    pv_mode = fields.Selection(
        [('draft', 'Draft the voucher for the payables clerk'), ('auto_submit', 'Submit a matched voucher for approval')],
        string='Payment voucher',
        default='draft',
        required=True,
    )

    @api.model
    def _singleton(self):
        return self.sudo().search([], limit=1) or self.sudo().create({})

    @api.model
    def mode(self, which):
        record = self._singleton()
        return record.grn_mode if which == 'grn' else record.pv_mode

    @api.model_create_multi
    def create(self, vals_list):
        if self.sudo().search_count([]):
            raise UserError(self.env._('There is only one set of receiving modes.'))
        return super().create(vals_list)

    def unlink(self):
        raise UserError(self.env._('The receiving modes cannot be deleted.'))
