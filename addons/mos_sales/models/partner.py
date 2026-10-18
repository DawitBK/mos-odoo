from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_credit_limit = fields.Float(string='Credit limit', help='Zero means no credit: every order is paid before release.')
    mos_credit_mode = fields.Selection(
        [('none', 'No check'), ('warn', 'Warn'), ('block', 'Block until approved')],
        string='Credit check',
        default='block',
        help='What happens when open invoices plus a new order exceed the limit.',
    )
    mos_owed = fields.Float(compute='_compute_mos_owed', string='Owes us')

    def _compute_mos_owed(self):
        rows = self.env['mos.customer.balance'].sudo()._read_group([('customer_id', 'in', self.ids)], ['customer_id'], ['balance:sum'])
        owed = {c.id: b for c, b in rows}
        for p in self:
            p.mos_owed = owed.get(p.id, 0.0)
