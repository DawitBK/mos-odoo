from odoo import fields, models
from odoo.exceptions import UserError


class MosCommercialWizard(models.TransientModel):
    """Reason dialog for the commercial acts that need one."""

    _name = 'mos.commercial.wizard'
    _description = 'MOS commercial action with a reason'

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    method = fields.Char(required=True, readonly=True)
    reason = fields.Char(required=True)

    ALLOWED = {
        ('sale.order', 'action_approve_prices'),
        ('res.partner', 'action_block_customer'),
        ('mos.customer.return', 'action_decide_restock'),
        ('mos.customer.return', 'action_decide_scrap'),
    }

    def action_confirm(self):
        self.ensure_one()
        if (self.res_model, self.method) not in self.ALLOWED:
            raise UserError(self.env._('That action is not available here.'))
        record = self.env[self.res_model].browse(self.res_id)
        getattr(record, self.method)(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class MosCommercialOpener(models.AbstractModel):
    _name = 'mos.commercial.opener'
    _description = 'Opens the commercial reason dialog'

    def action_commercial_ui(self):
        self.ensure_one()
        method = self.env.context.get('mos_method')
        if (self._name, method) not in MosCommercialWizard.ALLOWED:
            raise UserError(self.env._('That action is not available here.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env.context.get('mos_title') or method,
            'res_model': 'mos.commercial.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_method': method},
        }


class SaleOrderUi(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'mos.commercial.opener']


class ResPartnerUi(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'mos.commercial.opener']


class MosCustomerReturnUi(models.Model):
    _name = 'mos.customer.return'
    _inherit = ['mos.customer.return', 'mos.commercial.opener']

    def action_decide_restock(self, note=''):
        return self.action_decide('restock', note)

    def action_decide_scrap(self, note=''):
        return self.action_decide('scrap', note)
