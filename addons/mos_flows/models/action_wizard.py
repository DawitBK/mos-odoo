from odoo import fields, models
from odoo.exceptions import UserError

# (model, method) -> the arguments the dialog collects, in order: reason, number, order, basis, choice, text
ALLOWED = {
    ('mos.gate.visit', 'action_link_po'): ('order', 'reason'),
    ('mos.gate.visit', 'action_reject_at_gate'): ('reason',),
    ('mos.gate.visit', 'action_exit_without_tare'): ('reason', 'number'),
    ('mos.gate.visit', 'action_release_payment_block'): ('reason',),
    ('mos.gate.visit', 'action_abandon'): ('reason',),
    ('mos.gate.visit', 'action_resolve_weight_dispute'): ('basis', 'reason'),
    ('mos.gate.visit', 'action_reverse_grn'): ('reason',),
    ('mos.gate.visit', 'action_receive_quarantine'): ('number',),
    ('mos.gate.visit', 'action_approve_excess'): ('reason',),
    ('mos.gate.visit', 'action_supervisor_override'): ('reason',),
    ('mos.weigh.ticket', 'action_confirm_tare'): ('reason',),
    ('mos.weigh.ticket', 'action_approve_reweigh'): ('reason',),
    ('mos.lab.inspection', 'action_reopen'): ('reason',),
    ('mos.lab.sample', 'action_mark_lost'): ('reason',),
}


class MosActionWizard(models.TransientModel):
    """One small dialog for every act that needs a reason: it calls the model's own method, which does all the checking."""

    _name = 'mos.action.wizard'
    _description = 'MOS action with a reason'

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    method = fields.Char(required=True, readonly=True)
    title = fields.Char(readonly=True)
    needs = fields.Char(readonly=True)
    reason = fields.Char()
    number = fields.Float(digits=(14, 1))
    order_id = fields.Many2one('purchase.order', domain="[('state', 'in', ('purchase', 'done'))]")
    basis = fields.Selection([('bridge', 'Bridge weight'), ('declared', 'Declared weight')])

    def action_confirm(self):
        self.ensure_one()
        key = (self.res_model, self.method)
        if key not in ALLOWED:
            raise UserError(self.env._('That action is not available here.'))
        record = self.env[self.res_model].browse(self.res_id)
        values = {'reason': self.reason or '', 'number': self.number, 'order': self.order_id, 'basis': self.basis}
        args = [values[name] for name in ALLOWED[key]]
        getattr(record, self.method)(*args)
        return {'type': 'ir.actions.act_window_close'}


class MosActionOpener(models.AbstractModel):
    """Gives each model a way to open the dialog for one of its allowed methods."""

    _name = 'mos.action.opener'
    _description = 'Open the reason dialog'

    def action_ui(self):
        self.ensure_one()
        method = self.env.context.get('mos_method')
        if (self._name, method) not in ALLOWED:
            raise UserError(self.env._('That action is not available here.'))
        needs = ALLOWED[(self._name, method)]
        return {
            'type': 'ir.actions.act_window',
            'name': self.env.context.get('mos_title') or method,
            'res_model': 'mos.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id, 'default_method': method, 'default_needs': ','.join(needs)},
        }


class MosGateVisitUi(models.Model):
    _name = 'mos.gate.visit'
    _inherit = ['mos.gate.visit', 'mos.action.opener']


class MosWeighTicketUi(models.Model):
    _name = 'mos.weigh.ticket'
    _inherit = ['mos.weigh.ticket', 'mos.action.opener']


class MosLabInspectionUi(models.Model):
    _name = 'mos.lab.inspection'
    _inherit = ['mos.lab.inspection', 'mos.action.opener']


class MosLabSampleUi(models.Model):
    _name = 'mos.lab.sample'
    _inherit = ['mos.lab.sample', 'mos.action.opener']
