from math import ceil

from odoo import api, fields, models
from odoo.exceptions import UserError


class MosMrpSuggestion(models.Model):
    """What to make and what to buy so that open sales orders can be met: demand netted against stock and what is already
    planned or on order, each suggestion pegged to the orders behind it. Accepting one creates the work order or purchase request
    (FR-MFG-004). Suggestions are recomputed on demand; accepting or dismissing is the planner's decision."""

    _name = 'mos.mrp.suggestion'
    _inherit = ['mos.audited']
    _description = 'MOS planning suggestion'
    _order = 'kind, id'

    kind = fields.Selection([('work_order', 'Make'), ('purchase', 'Buy')], required=True)
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(digits=(14, 0), required=True)
    site_id = fields.Many2one('mos.site')
    reason = fields.Char()
    peg = fields.Char(string='For', help='The sales orders, or the suggested work orders, this is for.')
    parent_id = fields.Many2one('mos.mrp.suggestion', readonly=True, ondelete='cascade')
    state = fields.Selection([('open', 'Open'), ('accepted', 'Accepted'), ('dismissed', 'Dismissed')], default='open', required=True, readonly=True)
    result = fields.Char(readonly=True, help='The document that accepting it created.')

    # -- generation ---------------------------------------------------------------------------------------------------
    @api.model
    def generate(self):
        """Replace the open suggestions with a fresh set from today's demand and stock. Accepted and dismissed ones are history."""
        env = self.env
        env.flush_all()  # the planner and outstanding reports are SQL views: write pending changes first
        self.sudo().search([('state', '=', 'open')]).unlink()
        site = env.user._mos_current_site() or env['mos.site'].search([], limit=1)
        planner = env['mos.planner.report'].sudo().search([('shortfall', '>', 0)])
        wheat_need = {}
        made = self.browse()
        for row in planner:
            flour = row.product_id
            recipe = env['mos.recipe'].sudo().in_force(flour)
            if not recipe:
                continue
            planned_out = sum(
                order.planned_kg * order.recipe_id.target_extraction_pct / 100.0
                for order in env['mos.work.order'].sudo().search([('state', 'in', ('draft', 'planned', 'running')), ('product_id', '=', flour.id)])
            )
            net = row.shortfall - planned_out
            if net <= 0.5:
                continue
            wheat_kg = ceil(net / (recipe.target_extraction_pct / 100.0))
            orders = env['sale.order.line'].sudo().search([('product_id', '=', flour.id), ('order_id.state', '=', 'sale')]).mapped('order_id.name')
            suggestion = self.sudo().create(
                {
                    'kind': 'work_order',
                    'product_id': flour.id,
                    'qty': wheat_kg,
                    'site_id': site.id,
                    'reason': env._('Short by %(net)s kg of flour after stock and planned runs; %(wheat)s kg of wheat to mill.', net=round(net), wheat=wheat_kg),
                    'peg': ', '.join(sorted(set(orders))),
                }
            )
            made |= suggestion
            for rline in recipe.line_ids:
                wheat_need.setdefault(rline.wheat_id, []).append((wheat_kg * rline.share_pct / 100.0, suggestion))
        for wheat, needs in wheat_need.items():
            required = sum(qty for qty, _s in needs)
            if required <= 0:
                continue
            available = sum(env['stock.quant'].sudo().search([('product_id', '=', wheat.id), ('location_id.usage', '=', 'internal'), ('location_id.mos_stock_status', '=', 'available')]).mapped('quantity'))
            on_order = sum(env['mos.po.outstanding'].sudo().search([('product_id', '=', wheat.id)]).mapped('outstanding'))
            gap = required - available - on_order
            if gap > 0.5:
                gap = max(gap, wheat.product_tmpl_id.mos_reorder_qty)
                parents = [s for _q, s in needs if s]
                self.sudo().create(
                    {
                        'kind': 'purchase',
                        'product_id': wheat.id,
                        'qty': ceil(gap),
                        'site_id': site.id,
                        'reason': env._('Needs %(req)s kg, %(av)s kg available and %(oo)s kg on order.', req=round(required), av=round(available), oo=round(on_order)),
                        'peg': ', '.join(f'{s.product_id.display_name} #{s.id}' for s in parents),
                        'parent_id': parents[0].id if parents else False,
                    }
                )
        return self.sudo().search([('state', '=', 'open')])

    # -- decisions ----------------------------------------------------------------------------------------------------
    def action_accept(self):
        user = self.env.user
        for sug in self:
            if sug.state != 'open':
                raise UserError(self.env._('This suggestion has been dealt with already.'))
            if sug.kind == 'work_order':
                if not (user.has_group('mos_manufacturing.group_mos_production_supervisor') or user.has_group('mos_core.group_mos_admin')):
                    raise UserError(self.env._('Only a production supervisor can accept a suggested work order.'))
                recipe = self.env['mos.recipe'].in_force(sug.product_id)
                if not recipe:
                    raise UserError(self.env._('There is no approved recipe for %s.', sug.product_id.display_name))
                doc = self.env['mos.work.order'].create({'site_id': sug.site_id.id, 'recipe_id': recipe.id, 'planned_kg': sug.qty})
            else:
                if not (user.has_group('mos_procurement.group_mos_procurement_user') or user.has_group('mos_core.group_mos_admin')):
                    raise UserError(self.env._('Only procurement staff can accept a suggested purchase.'))
                doc = self.env['mos.purchase.request'].create(
                    {
                        'site_id': sug.site_id.id,
                        'reason': sug.reason or self.env._('Planning suggestion'),
                        'line_ids': [(0, 0, {'product_id': sug.product_id.id, 'qty': sug.qty, 'estimated_price': sug.product_id.standard_price})],
                    }
                )
            sug.sudo().write({'state': 'accepted', 'result': doc.display_name})
            self.env['mos.audit.event'].record('mrp_suggestion_accepted', sug, f'{sug.kind} {sug.product_id.display_name} {sug.qty} -> {doc.display_name}')
        return True

    def action_dismiss(self):
        for sug in self:
            if sug.state != 'open':
                raise UserError(self.env._('This suggestion has been dealt with already.'))
        self.sudo().write({'state': 'dismissed'})
