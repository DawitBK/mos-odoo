from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    mos_bag_kg = fields.Float(string='Bag weight (kg)', digits=(8, 3), help='For a packed product: the kilograms of flour in one unit.')
    mos_premix_id = fields.Many2one('product.product', string='Fortification premix', help='For flour that must be fortified: the premix dosed into it.')
    mos_premix_g_per_t = fields.Float(string='Premix (g per tonne)', help='Target dose; zero means the flour is not fortified.')
    mos_cost_factor = fields.Float(string='Cost share factor', default=1.0, help='How much of the wheat cost a kilogram of this co-product carries, relative to flour.')


class MosPackRun(models.Model):
    """Bulk flour is packed into bag products: the flour batch it came from is recorded, the bags become new batches and
    the label carries the batch number (FR-MFG-016)."""

    _name = 'mos.pack.run'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS packing run'
    _order = 'id desc'
    _mos_final_states = ('done',)
    _mos_editable_after_post = set()

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user.mos_site_ids[:1])
    work_centre_id = fields.Many2one('mos.work.centre', domain="[('kind', '=', 'packing')]")
    date = fields.Date(default=fields.Date.context_today, required=True)
    source_batch_id = fields.Many2one('mos.batch', required=True, domain="[('kind', '=', 'flour'), ('state', '=', 'released')]")
    flour_id = fields.Many2one(related='source_batch_id.product_id', store=True)
    line_ids = fields.One2many('mos.pack.run.line', 'run_id', string='Bags packed')
    spillage_kg = fields.Float(string='Spillage and sweepings (kg)', digits=(12, 1))
    packed_kg = fields.Float(compute='_compute_totals', store=True, digits=(14, 1))
    used_kg = fields.Float(string='Flour used (kg)', compute='_compute_totals', store=True, digits=(14, 1))
    yield_pct = fields.Float(string='Packing yield (%)', compute='_compute_totals', store=True, digits=(5, 2))
    state = fields.Selection([('draft', 'Planned'), ('done', 'Completed')], default='draft', required=True, readonly=True, tracking=True)

    @api.depends('line_ids.kg', 'spillage_kg')
    def _compute_totals(self):
        for run in self:
            run.packed_kg = sum(run.line_ids.mapped('kg'))
            run.used_kg = run.packed_kg + run.spillage_kg
            run.yield_pct = (run.packed_kg / run.used_kg * 100.0) if run.used_kg else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.pack.run')
        return super().create(vals_list)

    def action_complete(self):
        for run in self:
            if run.state != 'draft':
                raise UserError(self.env._('This run is already completed.'))
            if not run.line_ids:
                raise UserError(self.env._('Enter the bags packed.'))
            batch = run.source_batch_id
            if batch.state != 'released':
                raise UserError(self.env._('Batch %s has not been released by quality control.', batch.name))
            if run.used_kg > batch.remaining_qty + 0.0001:
                raise UserError(self.env._('Batch %(batch)s has %(have)s kg left and the run uses %(want)s kg.', batch=batch.name, have=batch.remaining_qty, want=run.used_kg))
            stock = run.site_id.warehouse_id.lot_stock_id
            available = self.env['stock.quant'].sudo()._get_available_quantity(run.flour_id, stock)
            if available + 0.0001 < run.used_kg:
                raise UserError(self.env._('Only %(have)s kg of %(flour)s is available to pack.', have=available, flour=run.flour_id.display_name))
            production = run.flour_id.property_stock_production
            run._move(run.flour_id, run.used_kg, stock, production)
            for line in run.line_ids:
                run._move(line.product_id, line.bags, line.product_id.property_stock_production, stock)
                packed = self.env['mos.batch'].sudo().create(
                    {'kind': 'packed', 'product_id': line.product_id.id, 'site_id': run.site_id.id, 'qty': line.bags, 'pack_run_id': run.id, 'state': 'released'}
                )
                self.env['mos.batch.link'].sudo().create({'child_id': packed.id, 'parent_id': batch.id, 'qty': line.kg + run.spillage_kg * (line.kg / run.packed_kg)})
                line.batch_id = packed
            run.write({'state': 'done'})
            self.env['mos.audit.event'].record('pack_run_completed', run, f'{run.name}: {run.packed_kg} kg packed from {batch.name}, yield {run.yield_pct:.2f}%')

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


class MosPackRunLine(models.Model):
    _name = 'mos.pack.run.line'
    _description = 'MOS packing run line'

    run_id = fields.Many2one('mos.pack.run', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, domain="[('mos_bag_kg', '>', 0)]", string='Bag product')
    bags = fields.Float(required=True, digits=(12, 0))
    bag_kg = fields.Float(related='product_id.mos_bag_kg', string='kg per bag')
    kg = fields.Float(compute='_compute_kg', store=True, digits=(14, 1))
    batch_id = fields.Many2one('mos.batch', readonly=True)

    @api.depends('bags', 'product_id.mos_bag_kg')
    def _compute_kg(self):
        for line in self:
            line.kg = line.bags * line.product_id.mos_bag_kg

    @api.constrains('bags')
    def _check_bags(self):
        for line in self:
            if line.bags <= 0:
                raise ValidationError(self.env._('Pack at least one bag.'))

    def _guard(self):
        if any(line.run_id.state == 'done' for line in self):
            raise UserError(self.env._('A completed run cannot be changed.'))

    @api.model_create_multi
    def create(self, vals_list):
        runs = self.env['mos.pack.run'].browse([v['run_id'] for v in vals_list if v.get('run_id')])
        if any(r.state == 'done' for r in runs):
            raise UserError(self.env._('A completed run cannot be changed.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard()
        return super().write(vals)

    def unlink(self):
        self._guard()
        return super().unlink()
