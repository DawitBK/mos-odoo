from odoo import fields, models
from odoo.exceptions import UserError


class MosRfqAwardWizard(models.TransientModel):
    _name = 'mos.rfq.award.wizard'
    _description = 'MOS award a request for quotation'

    rfq_id = fields.Many2one('mos.rfq', required=True, readonly=True)
    quote_id = fields.Many2one('mos.rfq.quote', required=True, string='Winning offer')
    reason = fields.Char()

    def action_confirm(self):
        self.ensure_one()
        order = self.rfq_id.action_award(self.quote_id, self.reason or '')
        return {'type': 'ir.actions.act_window', 'res_model': 'purchase.order', 'res_id': order.id, 'view_mode': 'form', 'target': 'current'}


class MosCloseShortWizard(models.TransientModel):
    _name = 'mos.close.short.wizard'
    _description = 'MOS close an order short'

    order_id = fields.Many2one('purchase.order', required=True, readonly=True)
    reason = fields.Char(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.order_id.action_close_short(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class MosRfqUi(models.Model):
    _inherit = 'mos.rfq'

    def action_open_award(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Award the order'),
            'res_model': 'mos.rfq.award.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_rfq_id': self.id},
        }


class PurchaseOrderUi(models.Model):
    _inherit = 'purchase.order'

    def action_open_close_short(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Close the order short'),
            'res_model': 'mos.close.short.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }


class MosSupplierInviteUi(models.Model):
    _inherit = 'mos.supplier.invite'

    def action_show_link(self):
        self.ensure_one()
        if self.state != 'open':
            raise UserError(self.env._('This invitation is no longer open.'))
        base = self.env['ir.config_parameter'].sudo().get_str('web.base.url')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': self.env._('Send this link to %s', self.name), 'message': base + self.path(), 'type': 'info', 'sticky': True},
        }
