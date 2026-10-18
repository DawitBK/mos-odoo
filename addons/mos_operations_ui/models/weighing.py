from odoo import api, fields, models
from odoo.exceptions import UserError

STEPS = [('first', 'First weighing'), ('second', 'Second weighing'), ('verify', 'Blind verification')]


class MosWeighWizard(models.TransientModel):
    """The weighbridge screen: the first weight, the second weight, then a second person confirms both without being shown them
    (FR-GATE-005..009). The ticket does all the checking; this only asks."""

    _name = 'mos.weigh.wizard'
    _description = 'MOS weighing'

    ticket_id = fields.Many2one('mos.weigh.ticket', required=True, readonly=True)
    step = fields.Selection(STEPS, required=True, readonly=True)
    instruction = fields.Char(readonly=True)
    weight = fields.Float(string='Weight (kg)', digits=(12, 1))
    weight_gross = fields.Float(string='Gross read on the scale (kg)', digits=(12, 1))
    weight_tare = fields.Float(string='Tare read on the scale (kg)', digits=(12, 1))

    def action_confirm(self):
        self.ensure_one()
        ticket = self.ticket_id
        if self.step == 'first':
            ticket.record_first(self.weight)
        elif self.step == 'second':
            ticket.record_second(self.weight)
        else:
            ticket.verify_blind(self.weight_gross, self.weight_tare)
        return {'type': 'ir.actions.act_window_close'}


class MosWeighTicket(models.Model):
    _inherit = 'mos.weigh.ticket'

    def _mos_next_step(self):
        self.ensure_one()
        if self.state == 'draft':
            return 'second' if self.first_weight else 'first'
        if self.state == 'weighed':
            return 'verify'
        return None

    def action_weigh_ui(self):
        """Open the weighing dialog for whatever this ticket needs next."""
        self.ensure_one()
        step = self._mos_next_step()
        if not step:
            raise UserError(self.env._('This ticket needs no more weighing (it is %s).', dict(self._fields['state'].selection)[self.state]))
        out = self.direction == 'out'
        gross_first = not out
        text = {
            'first': self.env._('Weigh the empty truck (tare).') if out else self.env._('Weigh the loaded truck (gross).'),
            'second': self.env._('Weigh the loaded truck (gross).') if out else self.env._('Weigh the empty truck (tare).'),
            'verify': self.env._('Without looking at what was recorded, type the gross and the tare you read on the scale.'),
        }[step]
        return {
            'type': 'ir.actions.act_window',
            'name': dict(STEPS)[step],
            'res_model': 'mos.weigh.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_ticket_id': self.id, 'default_step': step, 'default_instruction': text, 'mos_gross_first': gross_first},
        }


class MosGateVisit(models.Model):
    _inherit = 'mos.gate.visit'

    ticket_stage = fields.Selection(
        [('none', 'No ticket'), ('first', 'Waiting for the first weighing'), ('second', 'Waiting for the second weighing'), ('verify', 'Waiting for verification'), ('posted', 'Weighing complete')],
        compute='_compute_ticket_stage',
    )

    @api.depends('ticket_id', 'ticket_id.state', 'ticket_id.first_weight', 'ticket_id.second_weight')
    def _compute_ticket_stage(self):
        for visit in self:
            ticket = visit.ticket_id
            visit.ticket_stage = 'none' if not ticket else ticket._mos_next_step() or 'posted'

    def action_weigh_ui(self):
        """The weighing dialog for this visit's current ticket."""
        self.ensure_one()
        if not self.ticket_id:
            raise UserError(self.env._('Start the weighing first.'))
        return self.ticket_id.action_weigh_ui()


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def mos_visible_actions(self):
        """The menus with an action that this person actually sees in the menu bar, for the browser checks."""
        menus = self.search([('action', '!=', False)])._filter_visible_menus()
        visible = self._visible_menu_ids(False)

        def reachable(menu):  # every folder above it must be visible too, or the menu bar never shows it
            while menu.parent_id:
                menu = menu.parent_id
                if menu.id not in visible:
                    return False
            return True

        return [{'complete_name': menu._get_full_name(), 'action': f'{menu.action._name},{menu.action.id}'} for menu in menus if reachable(menu)]
