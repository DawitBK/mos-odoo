from odoo import fields, models


class MosBatchDecideWizard(models.TransientModel):
    _name = 'mos.batch.decide.wizard'
    _description = 'MOS release or reject a batch'

    inspection_id = fields.Many2one('mos.batch.inspection', required=True, readonly=True)
    suggested = fields.Selection(related='inspection_id.suggested')
    decision = fields.Selection([('release', 'Release'), ('reject', 'Reject')], required=True, default='release')
    note = fields.Char(help='Required to release a batch that failed its specification.')

    def action_confirm(self):
        self.ensure_one()
        self.inspection_id.decide(self.decision, self.note or '')
        return {'type': 'ir.actions.act_window_close'}


class MosBatchInspection(models.Model):
    _inherit = 'mos.batch.inspection'

    def action_open_decide(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Release or reject'),
            'res_model': 'mos.batch.decide.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_inspection_id': self.id},
        }
