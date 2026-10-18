import base64

from odoo import fields, models

from odoo.addons.mos_flows.models.action_wizard import ALLOWED

# More acts that were possible in code but had no screen. Each is (model, method): the arguments the dialog collects, in order.
# Kinds: reason, number, order, basis (the existing ones), text, date, supplier, decision, file (name and content), reweigh_reason.
ALLOWED.update(
    {
        ('mos.weigh.ticket', 'reweigh'): ('reweigh_reason',),
        ('mos.weigh.ticket', 'action_attach_photo'): ('filename', 'file'),
        ('mos.exception', 'action_resolve'): ('reason',),
        ('mos.exception', 'action_dismiss'): ('reason',),
        ('mos.lab.inspection', 'action_request_revision'): ('decision', 'number', 'reason'),
        ('mos.lab.sample', 'action_discard'): ('reason',),
        ('mos.lab.coa', 'action_revoke'): ('reason',),
        ('mos.cheque.leaf', 'action_void'): ('reason',),
        ('mos.purchase.contract', 'action_new_calloff'): ('number',),
        ('mos.purchase.contract', 'action_close'): ('reason',),
        ('mos.item.conversion', 'action_new_version'): ('number', 'date'),
        ('mos.period.lock', 'set_soft_lock'): ('date', 'reason'),
        ('mos.purchase.request', 'action_convert'): ('supplier',),
        ('stock.lot', 'action_split'): ('number', 'text'),
    }
)


class MosActionWizard(models.TransientModel):
    _inherit = 'mos.action.wizard'

    text = fields.Char(string='Name')
    date = fields.Date()
    partner_id = fields.Many2one('res.partner', string='Supplier', domain="[('supplier_rank', '>', 0)]")
    decision = fields.Selection([('accept', 'Accept as it is'), ('accept_deduction', 'Accept with a deduction'), ('reject', 'Reject')])
    filename = fields.Char(string='File name')
    file = fields.Binary(string='File')
    reweigh_reason_id = fields.Many2one('mos.reason', string='Why', domain="[('domain', '=', 'reweigh')]")

    def _mos_values(self):
        values = super()._mos_values()
        raw = self.file
        raw = raw.content if hasattr(raw, 'content') else raw
        if isinstance(raw, str):
            raw = raw.encode()
        values.update(
            {
                'text': self.text or None,
                'date': self.date,
                'supplier': self.partner_id,
                'decision': self.decision,
                'filename': self.filename or '',
                'file': base64.b64encode(raw).decode() if raw else '',
                'reweigh_reason': self.reweigh_reason_id,
            }
        )
        return values


class ExceptionUi(models.Model):
    _name = 'mos.exception'
    _inherit = ['mos.exception', 'mos.action.opener']


class CoaUi(models.Model):
    _name = 'mos.lab.coa'
    _inherit = ['mos.lab.coa', 'mos.action.opener']


class ChequeLeafUi(models.Model):
    _name = 'mos.cheque.leaf'
    _inherit = ['mos.cheque.leaf', 'mos.action.opener']


class ContractUi(models.Model):
    _name = 'mos.purchase.contract'
    _inherit = ['mos.purchase.contract', 'mos.action.opener']


class ConversionUi(models.Model):
    _name = 'mos.item.conversion'
    _inherit = ['mos.item.conversion', 'mos.action.opener']


class PeriodLockUi(models.Model):
    _name = 'mos.period.lock'
    _inherit = ['mos.period.lock', 'mos.action.opener']


class PurchaseRequestUi(models.Model):
    _name = 'mos.purchase.request'
    _inherit = ['mos.purchase.request', 'mos.action.opener']


class LotUi(models.Model):
    _name = 'stock.lot'
    _inherit = ['stock.lot', 'mos.action.opener']

