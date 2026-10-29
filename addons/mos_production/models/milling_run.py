from odoo import api, fields, models
from odoo.exceptions import UserError


class MosMillingRun(models.Model):
    """One milling run: wheat is drawn from silos, flour and by-products come out.

    Completing the run posts the stock movements (wheat leaves the silos, flour and bran enter the
    site's stock) and fixes the figures: a completed run is never edited, a mistake is a new
    correcting run (FR-PLT-021). The extraction rate is flour out over wheat in, the number a miller
    watches every day (FR-MFG-013).
    """

    _name = 'mos.milling.run'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS milling run'
    _order = 'run_date desc, id desc'
    _mos_final_states = ('done',)
    _mos_editable_after_post = set()

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user._mos_current_site())
    run_date = fields.Date(required=True, default=lambda self: self._default_run_date())
    shift = fields.Selection([('day', 'Day'), ('night', 'Night')], default='day', required=True)
    state = fields.Selection([('draft', 'Planned'), ('done', 'Completed')], default='draft', required=True, readonly=True, tracking=True)
    input_ids = fields.One2many('mos.milling.input', 'run_id', string='Wheat drawn')
    output_ids = fields.One2many('mos.milling.output', 'run_id', string='Products')
    wheat_kg = fields.Float(string='Wheat in (kg)', compute='_compute_totals', store=True, digits=(14, 0))
    flour_kg = fields.Float(string='Flour out (kg)', compute='_compute_totals', store=True, digits=(14, 0))
    bran_kg = fields.Float(string='Bran and offal (kg)', compute='_compute_totals', store=True, digits=(14, 0))
    loss_kg = fields.Float(string='Loss (kg)', compute='_compute_totals', store=True, digits=(14, 0), help='Moisture, dust and weighing difference.')
    extraction_pct = fields.Float(string='Extraction (%)', compute='_compute_totals', store=True, digits=(5, 2))

    def _default_run_date(self):
        # The date at the mill (the site's time zone), not in Greenwich (FR-PLT-004).
        site = self.env.user._mos_current_site() or self.env['mos.site'].search([], limit=1)
        return site.business_date() if site else fields.Date.context_today(self)

    @api.depends('input_ids.qty_kg', 'output_ids.qty_kg', 'output_ids.kind')
    def _compute_totals(self):
        for run in self:
            wheat = sum(run.input_ids.mapped('qty_kg'))
            flour = sum(run.output_ids.filtered(lambda o: o.kind == 'flour').mapped('qty_kg'))
            bran = sum(run.output_ids.filtered(lambda o: o.kind != 'flour').mapped('qty_kg'))
            run.wheat_kg, run.flour_kg, run.bran_kg = wheat, flour, bran
            run.loss_kg = wheat - flour - bran
            run.extraction_pct = (flour / wheat * 100.0) if wheat else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.milling.run')
        return super().create(vals_list)

    def action_complete(self):
        for run in self:
            if run.state != 'draft':
                raise UserError(self.env._('This run is already completed.'))
            if not run.input_ids or not run.output_ids:
                raise UserError(self.env._('Enter the wheat drawn and the products made before completing the run.'))
            if run.wheat_kg <= 0 or run.flour_kg <= 0:
                raise UserError(self.env._('A run needs wheat in and flour out.'))
            if run.flour_kg + run.bran_kg > run.wheat_kg:
                raise UserError(self.env._('The products weigh more than the wheat drawn: check the figures.'))
            # Wheat can only come out of a silo that holds it.
            drawn = {}
            for line in run.input_ids:
                drawn[line.silo_id] = drawn.get(line.silo_id, 0.0) + line.qty_kg
            for silo, qty in drawn.items():
                silo.fresh()
                if qty > silo.level_kg + 0.0001:
                    raise UserError(
                        self.env._('Silo %(silo)s holds %(have)s kg but the run draws %(want)s kg.', silo=silo.code, have=round(silo.level_kg), want=round(qty))
                    )
                if silo.safety_stock_kg and silo.level_kg - qty < silo.safety_stock_kg - 0.0001:
                    raise UserError(
                        self.env._('Silo %(silo)s must keep a safety stock of %(safe)s kg: the run would leave %(left)s kg.', silo=silo.code, safe=round(silo.safety_stock_kg), left=round(silo.level_kg - qty))
                    )
            for line in run.input_ids:
                run._move(line.product_id, line.qty_kg, line.silo_id.location_id, line.product_id.property_stock_production)
            stock_location = run.site_id.warehouse_id.lot_stock_id
            for out in run.output_ids:
                run._move(out.product_id, out.qty_kg, out.product_id.property_stock_production, stock_location)
            run.write({'state': 'done'})
            self.env['mos.audit.event'].record(
                'milling_run_completed', run, f'{run.name}: {run.wheat_kg:.0f} kg wheat, {run.flour_kg:.0f} kg flour, extraction {run.extraction_pct:.2f}%'
            )

    def _move(self, product, qty, source, destination):
        move = self.env['stock.move'].sudo().create(
            {
                'product_id': product.id,
                'product_uom_qty': qty,
                'uom_id': product.uom_id.id,
                'location_id': source.id,
                'location_dest_id': destination.id,
                'origin': self.name,
                'quantity': qty,
                'picked': True,
            }
        )
        move._action_confirm()
        move._action_done()
        return move


class MosMillingInput(models.Model):
    _name = 'mos.milling.input'
    _description = 'MOS milling run input'
    _order = 'id'

    run_id = fields.Many2one('mos.milling.run', required=True, ondelete='cascade')
    silo_id = fields.Many2one('mos.silo', required=True, string='Silo')
    product_id = fields.Many2one('product.product', string='Wheat', required=True)
    qty_kg = fields.Float(string='Quantity (kg)', required=True, digits=(14, 0))

    @api.onchange('silo_id')
    def _onchange_silo(self):
        if self.silo_id.product_id and not self.product_id:
            self.product_id = self.silo_id.product_id

    @api.constrains('qty_kg')
    def _check_qty(self):
        for line in self:
            if line.qty_kg <= 0:
                raise UserError(self.env._('Quantities must be above zero.'))

    def _guard(self):
        if any(line.run_id.state == 'done' for line in self):
            raise UserError(self.env._('A completed run cannot be changed.'))

    @api.model_create_multi
    def create(self, vals_list):
        runs = self.env['mos.milling.run'].browse([v['run_id'] for v in vals_list if v.get('run_id')])
        if any(r.state == 'done' for r in runs):
            raise UserError(self.env._('A completed run cannot be changed.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard()
        return super().write(vals)

    def unlink(self):
        self._guard()
        return super().unlink()


class MosMillingOutput(models.Model):
    _name = 'mos.milling.output'
    _description = 'MOS milling run output'
    _order = 'id'

    run_id = fields.Many2one('mos.milling.run', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    kind = fields.Selection([('flour', 'Flour'), ('bran', 'Bran'), ('offal', 'Offal')], default='flour', required=True)
    qty_kg = fields.Float(string='Quantity (kg)', required=True, digits=(14, 0))

    @api.constrains('qty_kg')
    def _check_qty(self):
        for line in self:
            if line.qty_kg <= 0:
                raise UserError(self.env._('Quantities must be above zero.'))

    def _guard(self):
        if any(line.run_id.state == 'done' for line in self):
            raise UserError(self.env._('A completed run cannot be changed.'))

    @api.model_create_multi
    def create(self, vals_list):
        runs = self.env['mos.milling.run'].browse([v['run_id'] for v in vals_list if v.get('run_id')])
        if any(r.state == 'done' for r in runs):
            raise UserError(self.env._('A completed run cannot be changed.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard()
        return super().write(vals)

    def unlink(self):
        self._guard()
        return super().unlink()
