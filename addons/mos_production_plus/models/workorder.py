from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosException(models.Model):
    _inherit = 'mos.exception'

    kind = fields.Selection(selection_add=[('off_recipe', 'Run off recipe'), ('dip_variance', 'Silo dip differs from the ledger')], ondelete={'off_recipe': 'cascade', 'dip_variance': 'cascade'})


class MosWorkOrder(models.Model):
    """What the mill is going to do on a shift: mill this much wheat to this recipe on this line. Starting it opens the milling run
    with the grist drawn from the silos; finishing it compares the plan with what happened (FR-MFG-005, 011)."""

    _name = 'mos.work.order'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS work order'
    _order = 'date desc, id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user._mos_current_site())
    kind = fields.Selection([('standard', 'Batch'), ('campaign', 'Campaign (a shift or a day)')], default='campaign', required=True)
    recipe_id = fields.Many2one('mos.recipe', required=True, domain="[('state', '=', 'approved')]")
    product_id = fields.Many2one(related='recipe_id.product_id', store=True, string='Flour')
    planned_kg = fields.Float(string='Wheat to mill (kg)', required=True, digits=(14, 0))
    date = fields.Date(required=True, default=fields.Date.context_today)
    shift = fields.Selection([('day', 'Day'), ('night', 'Night')], default='day', required=True)
    work_centre_id = fields.Many2one('mos.work.centre', domain="[('kind', '=', 'milling')]")
    supervisor_id = fields.Many2one('res.users', default=lambda self: self.env.user)
    line_ids = fields.One2many('mos.work.order.line', 'order_id', string='Wheat to draw')
    run_id = fields.Many2one('mos.milling.run', readonly=True, copy=False)
    actual_wheat_kg = fields.Float(related='run_id.wheat_kg', string='Wheat milled (kg)')
    actual_flour_kg = fields.Float(related='run_id.flour_kg', string='Flour made (kg)')
    actual_extraction = fields.Float(related='run_id.extraction_pct', string='Extraction (%)')
    state = fields.Selection([('draft', 'Draft'), ('planned', 'Planned'), ('running', 'Running'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('planned_kg', 0) <= 0:
                raise ValidationError(self.env._('Plan a quantity above zero.'))
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.work.order')
        return super().create(vals_list)

    def action_plan(self):
        """Split the wheat over the silos that hold it, following the recipe's shares."""
        for order in self:
            if order.state != 'draft':
                raise UserError(self.env._('Only a draft order can be planned.'))
            order.line_ids.unlink()
            lines = []
            for rline in order.recipe_id.line_ids:
                need = order.planned_kg * rline.share_pct / 100.0
                silos = self.env['mos.silo'].search([('site_id', '=', order.site_id.id), ('product_id', '=', rline.wheat_id.id)], order='id')
                for silo in silos:
                    if need <= 0.0001:
                        break
                    if silo.fresh().level_kg <= 0 or self.env['mos.silo.hold'].is_held(silo):
                        continue
                    take = min(need, silo.level_kg - sum(l['qty_kg'] for l in lines if l['silo_id'] == silo.id))
                    if take > 0:
                        lines.append({'order_id': order.id, 'wheat_id': rline.wheat_id.id, 'silo_id': silo.id, 'qty_kg': round(take)})
                        need -= take
                if need > 1.0:
                    raise UserError(self.env._('The silos hold %(short)s kg too little %(wheat)s for this order.', short=round(need), wheat=rline.wheat_id.display_name))
            self.env['mos.work.order.line'].create(lines)
            order.state = 'planned'

    def action_start(self):
        for order in self:
            if order.state != 'planned':
                raise UserError(self.env._('Plan the order first.'))
            run = self.env['mos.milling.run'].create(
                {
                    'site_id': order.site_id.id,
                    'run_date': order.date,
                    'shift': order.shift,
                    'work_centre_id': order.work_centre_id.id,
                    'recipe_id': order.recipe_id.id,
                    'work_order_id': order.id,
                    'input_ids': [Command.create({'silo_id': l.silo_id.id, 'product_id': l.wheat_id.id, 'qty_kg': l.qty_kg}) for l in order.line_ids],
                }
            )
            order.write({'run_id': run.id, 'state': 'running'})
        return True

    def action_finish(self):
        for order in self:
            if order.state != 'running':
                raise UserError(self.env._('Only a running order can be finished.'))
            if order.run_id.state != 'done':
                raise UserError(self.env._('Complete the milling run %s first.', order.run_id.name))
            order.state = 'done'
            variance = order.actual_wheat_kg - order.planned_kg
            order.message_post(body=self.env._('Finished: %(got)s kg milled against %(plan)s kg planned (%(diff)+d kg).', got=round(order.actual_wheat_kg), plan=round(order.planned_kg), diff=round(variance)))

    def action_cancel(self):
        for order in self:
            if order.state in ('done', 'cancelled'):
                raise UserError(self.env._('This order is closed.'))
            if order.run_id and order.run_id.state == 'done':
                raise UserError(self.env._('The run is complete; finish the order instead.'))
            if order.run_id and order.run_id.state == 'draft':
                order.run_id.sudo().unlink()
            order.state = 'cancelled'


class MosWorkOrderLine(models.Model):
    _name = 'mos.work.order.line'
    _description = 'MOS work order wheat line'

    order_id = fields.Many2one('mos.work.order', required=True, ondelete='cascade')
    wheat_id = fields.Many2one('product.product', required=True)
    silo_id = fields.Many2one('mos.silo', required=True)
    qty_kg = fields.Float(required=True, digits=(14, 0))

    @api.constrains('qty_kg')
    def _check(self):
        for line in self:
            if line.qty_kg <= 0:
                raise ValidationError(self.env._('Draw a quantity above zero.'))
