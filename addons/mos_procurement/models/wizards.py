from odoo import fields, models


class MosSupplierStatusWizard(models.TransientModel):
    _name = 'mos.supplier.status.wizard'
    _description = 'MOS change supplier standing'

    partner_id = fields.Many2one('res.partner', required=True, readonly=True)
    action = fields.Selection([('suspend', 'Suspend'), ('block', 'Block'), ('reinstate', 'Reinstate as candidate')], required=True, default='suspend')
    reason = fields.Char()

    def action_confirm(self):
        self.ensure_one()
        partner = self.partner_id
        if self.action == 'suspend':
            partner.action_suspend_supplier(self.reason)
        elif self.action == 'block':
            partner.action_block_supplier(self.reason)
        else:
            partner.action_reinstate_supplier()
        return {'type': 'ir.actions.act_window_close'}


class MosBankVerifyWizard(models.TransientModel):
    _name = 'mos.supplier.bank.verify.wizard'
    _description = 'MOS record the bank call-back'

    change_id = fields.Many2one('mos.supplier.bank.change', required=True, readonly=True)
    note = fields.Char(string='Who was called, on which number, what they confirmed', required=True)

    def action_confirm(self):
        self.ensure_one()
        self.change_id.action_verify(self.note)
        return {'type': 'ir.actions.act_window_close'}


class MosReturnCreditWizard(models.TransientModel):
    _name = 'mos.supplier.return.credit.wizard'
    _description = 'MOS credit for returned goods'

    return_id = fields.Many2one('mos.supplier.return', required=True, readonly=True)
    purchase_id = fields.Many2one(related='return_id.purchase_id')
    invoice_id = fields.Many2one('mos.supplier.invoice', required=True, domain="[('purchase_id', '=', purchase_id)]")

    def action_confirm(self):
        self.ensure_one()
        self.return_id.action_credit(self.invoice_id)
        return {'type': 'ir.actions.act_window_close'}


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_open_status(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Supplier standing'),
            'res_model': 'mos.supplier.status.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_partner_id': self.id},
        }


class MosSupplierBankChange(models.Model):
    _inherit = 'mos.supplier.bank.change'

    def action_open_verify(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Call-back verification'),
            'res_model': 'mos.supplier.bank.verify.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_change_id': self.id},
        }


class MosSupplierReturn(models.Model):
    _inherit = 'mos.supplier.return'

    def action_open_credit(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Credit for returned goods'),
            'res_model': 'mos.supplier.return.credit.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_return_id': self.id},
        }
