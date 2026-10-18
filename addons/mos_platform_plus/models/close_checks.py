from odoo import fields, models
from odoo.exceptions import UserError


class MosCloseChecklist(models.TransientModel):
    """Everything that must be settled before a period is closed, counted live (FR-INV-012)."""

    _name = 'mos.close.checklist'
    _description = 'MOS period close checklist'

    date_to = fields.Date(string='Period ends', required=True, default=fields.Date.context_today)
    line_ids = fields.One2many('mos.close.checklist.line', 'checklist_id')
    ready = fields.Boolean(readonly=True)

    def _checks(self):
        """(label, model, domain, why it matters); other modules may extend this list."""
        env, end = self.env, self.date_to
        return [
            (env._('Receipts not finished'), 'stock.picking', [('picking_type_code', '=', 'incoming'), ('state', 'in', ('assigned', 'confirmed', 'waiting')), ('scheduled_date', '<=', fields.Datetime.to_datetime(end).replace(hour=23, minute=59))], env._('Goods that arrived but are not yet in stock.')),
            (env._('Stock adjustments waiting'), 'mos.stock.adjustment', [('state', 'in', ('draft', 'pending', 'approved')), ('date', '<=', end)], env._('Counts and write-offs that change the stock figures.')),
            (env._('Cycle counts not closed'), 'mos.cycle.count', [('state', 'in', ('draft', 'counting', 'submitted'))], env._('A count in progress means the book figure is not final.')),
            (env._('Silo dips off the ledger'), 'mos.silo.dip', [('within_tolerance', '=', False), ('adjustment_id', '=', False)], env._('A measured level that disagrees with the ledger and has no adjustment.')),
            (env._('Vouchers not settled'), 'mos.payment.voucher', [('state', 'in', ('submitted', 'approved', 'partially_paid'))], env._('Payments still to approve or make.')),
            (env._('Credit notes waiting'), 'mos.credit.note', [('state', 'in', ('draft', 'pending', 'approved'))], env._('Credits not yet posted to the customer.')),
            (env._('Exceptions still open'), 'mos.exception', [('state', 'in', ('open', 'assigned')), ('severity', '=', 'high')], env._('High-severity process exceptions nobody has closed.')),
        ]

    def action_run(self):
        self.ensure_one()
        lines = []
        for label, model, domain, why in self._checks():
            if model not in self.env:
                continue
            count = self.env[model].sudo().search_count(domain)  # a count only: the rows are opened under the person's own rights
            lines.append({'checklist_id': self.id, 'name': label, 'res_model': model, 'domain': repr(domain), 'count': count, 'why': why})
        self.line_ids.unlink()
        self.env['mos.close.checklist.line'].create(lines)
        self.ready = not any(line['count'] for line in lines)
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'current'}

    def action_sign_off(self):
        """Record that the period was checked and was clean; refused while anything is open."""
        self.ensure_one()
        if not self.line_ids:
            self.action_run()
        if not self.ready:
            raise UserError(self.env._('The period cannot be signed off while any check has items.'))
        self.env['mos.audit.event'].record('period_close_checked', self, f'Close checklist clean for the period ending {self.date_to}')
        return True


class MosCloseChecklistLine(models.TransientModel):
    _name = 'mos.close.checklist.line'
    _description = 'MOS close checklist line'

    checklist_id = fields.Many2one('mos.close.checklist', required=True, ondelete='cascade')
    name = fields.Char()
    res_model = fields.Char()
    domain = fields.Char()
    count = fields.Integer()
    why = fields.Char()

    def action_open(self):
        self.ensure_one()
        from odoo.tools.safe_eval import safe_eval

        return {'type': 'ir.actions.act_window', 'name': self.name, 'res_model': self.res_model, 'view_mode': 'list,form', 'domain': safe_eval(self.domain, {'False': False, 'True': True})}
