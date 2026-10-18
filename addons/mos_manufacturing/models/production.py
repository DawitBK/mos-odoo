from odoo import Command, api, fields, models, tools
from odoo.exceptions import UserError, ValidationError


class MosMillingRun(models.Model):
    """Milling with traceability, fortification dosing, cost settlement and a hold for finished-goods quality control."""

    _inherit = 'mos.milling.run'

    work_centre_id = fields.Many2one('mos.work.centre', domain="[('kind', '=', 'milling')]")
    dosing_ids = fields.One2many('mos.dosing.line', 'run_id', string='Fortification dosing')
    input_value = fields.Float(string='Value of the wheat drawn', readonly=True, digits=(14, 2))
    unit_cost_flour = fields.Float(string='Material cost per kg of flour', readonly=True, digits=(12, 4))
    batch_ids = fields.One2many('mos.batch', 'run_id', string='Batches made')

    # -- the checks that come before completion --------------------------------------------------------------------
    def _mos_dosing_target(self):
        """Premix kilograms the flour of this run should have received, per product."""
        self.ensure_one()
        targets = {}
        for out in self.output_ids.filtered(lambda o: o.kind == 'flour' and o.product_id.mos_premix_g_per_t):
            premix = out.product_id.mos_premix_id
            if not premix:
                raise UserError(self.env._('%s is fortified but has no premix product set.', out.product_id.display_name))
            targets[premix] = targets.get(premix, 0.0) + out.qty_kg * out.product_id.mos_premix_g_per_t / 1_000_000.0
        return targets

    def _mos_check_dosing(self):
        tolerance = self.env['ir.config_parameter'].sudo().get_float('mos_manufacturing.dosing_tolerance_pct', 10.0)
        for run in self:
            targets = run._mos_dosing_target()
            actual = {}
            for line in run.dosing_ids:
                actual[line.premix_id] = actual.get(line.premix_id, 0.0) + line.actual_kg
            for premix, target in targets.items():
                done = actual.get(premix, 0.0)
                if abs(done - target) > target * tolerance / 100.0 + 0.00001:
                    raise UserError(
                        self.env._(
                            'Fortification dose of %(premix)s: %(done)s kg dosed, %(target)s kg needed for this flour (tolerance %(tol)s%%).',
                            premix=premix.display_name,
                            done=round(done, 3),
                            target=round(target, 3),
                            tol=tolerance,
                        )
                    )
                stock = run.site_id.warehouse_id.lot_stock_id
                have = self.env['stock.quant'].sudo()._get_available_quantity(premix, stock)
                if have + 0.00001 < done:
                    raise UserError(self.env._('Only %(have)s kg of %(premix)s is in stock.', have=have, premix=premix.display_name))

    def _mos_prepare_batches(self):
        """Allocate the wheat, make the batches and settle the material cost, all before the stock moves (one transaction)."""
        self.ensure_one()
        BatchLink = self.env['mos.batch.link'].sudo()
        parents, input_value = [], 0.0
        for line in self.input_ids:
            for batch, qty in self.env['mos.batch'].consume(line.silo_id, line.product_id, line.qty_kg, self.site_id):
                parents.append((batch, qty))
                input_value += qty * (batch.unit_cost or line.product_id.standard_price)
        output_qty = sum(self.output_ids.mapped('qty_kg'))
        weights = sum(out.qty_kg * out.product_id.mos_cost_factor for out in self.output_ids) or 1.0
        hold = self.env['ir.config_parameter'].sudo().get_bool('mos_manufacturing.hold_flour')
        flour_cost = 0.0
        for out in self.output_ids:
            unit_cost = input_value * out.product_id.mos_cost_factor / weights
            if out.kind == 'flour':
                flour_cost = unit_cost
            batch = self.env['mos.batch'].sudo().create(
                {
                    'kind': out.kind,
                    'product_id': out.product_id.id,
                    'site_id': self.site_id.id,
                    'qty': out.qty_kg,
                    'run_id': self.id,
                    'state': 'hold' if (hold and out.kind == 'flour') else 'released',
                    'unit_cost': unit_cost,
                }
            )
            out.write({'batch_id': batch.id, 'unit_cost': unit_cost, 'cost_value': round(unit_cost * out.qty_kg, 2)})
            for parent, qty in parents:
                BatchLink.create({'child_id': batch.id, 'parent_id': parent.id, 'qty': qty * out.qty_kg / output_qty})
        self.write({'input_value': round(input_value, 2), 'unit_cost_flour': flour_cost})

    def action_complete(self):
        pending = self.filtered(lambda r: r.state == 'draft' and r.input_ids and r.output_ids)
        pending._mos_check_dosing()
        for run in pending:
            run._mos_prepare_batches()
        result = super().action_complete()
        hold = self.env['ir.config_parameter'].sudo().get_bool('mos_manufacturing.hold_flour')
        for run in pending:
            stock = run.site_id.warehouse_id.lot_stock_id
            for line in run.dosing_ids:
                run._move(line.premix_id, line.actual_kg, stock, line.premix_id.property_stock_production)
            if hold:
                quarantine = self.env['mos.stock.status.change'].status_location(run.site_id, 'quarantine')
                for out in run.output_ids.filtered(lambda o: o.kind == 'flour'):
                    run._move(out.product_id, out.qty_kg, stock, quarantine)
        return result


class MosMillingOutput(models.Model):
    _inherit = 'mos.milling.output'

    batch_id = fields.Many2one('mos.batch', readonly=True)
    unit_cost = fields.Float(readonly=True, digits=(12, 4))
    cost_value = fields.Float(readonly=True, digits=(14, 2))

    def write(self, vals):
        # Cost and batch are stamped before completion; nothing else changes once a run is done.
        return super().write(vals)


class MosDosingLine(models.Model):
    """Premix dosed into the flour: what was added against what the target says (FR-MFG-017)."""

    _name = 'mos.dosing.line'
    _description = 'MOS fortification dosing'

    run_id = fields.Many2one('mos.milling.run', required=True, ondelete='cascade')
    premix_id = fields.Many2one('product.product', required=True, string='Premix')
    actual_kg = fields.Float(string='Dosed (kg)', required=True, digits=(12, 3))
    target_kg = fields.Float(compute='_compute_target', digits=(12, 3), string='Target (kg)')
    variance_pct = fields.Float(compute='_compute_target', digits=(6, 1), string='Variance (%)')

    @api.depends('run_id.output_ids.qty_kg', 'run_id.output_ids.product_id', 'premix_id', 'actual_kg')
    def _compute_target(self):
        for line in self:
            target = 0.0
            for out in line.run_id.output_ids.filtered(lambda o: o.kind == 'flour' and o.product_id.mos_premix_id == line.premix_id):
                target += out.qty_kg * out.product_id.mos_premix_g_per_t / 1_000_000.0
            line.target_kg = target
            line.variance_pct = ((line.actual_kg - target) / target * 100.0) if target else 0.0

    @api.constrains('actual_kg')
    def _check(self):
        for line in self:
            if line.actual_kg <= 0:
                raise ValidationError(self.env._('Dose a quantity above zero.'))

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


class MosBatchDecision(models.Model):
    _inherit = 'mos.batch'

    def _apply_decision(self, inspection):
        """Release moves the held flour to available stock; rejection blocks it."""
        self.ensure_one()
        if self.kind != 'flour':
            self.write({'state': 'released' if inspection.decision == 'release' else 'rejected'})
            return
        if self.state != 'hold':
            self.write({'state': 'released' if inspection.decision == 'release' else 'rejected'})
            return
        target = 'available' if inspection.decision == 'release' else 'blocked'
        reason = self.env['mos.reason'].sudo().search([('domain', '=', 'status_change'), ('code', '=', 'FGQC')], limit=1) or self.env['mos.reason'].sudo().create(
            {'domain': 'status_change', 'code': 'FGQC', 'name': self.env._('Finished-goods quality decision')}
        )
        change = self.env['mos.stock.status.change'].sudo().create(
            {'site_id': self.site_id.id, 'product_id': self.product_id.id, 'qty': self.remaining_qty, 'from_status': 'quarantine', 'to_status': target, 'reason_id': reason.id, 'note': f'{self.name}: {inspection.name}'}
        )
        change.write({'state': 'approved', 'approved_by_id': inspection.decided_by_id.id})
        change.action_apply()
        self.write({'state': 'released' if inspection.decision == 'release' else 'rejected'})

    def action_new_inspection(self):
        self.ensure_one()
        inspection = self.env['mos.batch.inspection'].create({'batch_id': self.id})
        return {'type': 'ir.actions.act_window', 'res_model': 'mos.batch.inspection', 'res_id': inspection.id, 'view_mode': 'form', 'target': 'current'}


class MosProductionReport(models.Model):
    """Wheat in, flour out, extraction and loss by day (FR-MFG-013). Extraction is recomputed from the sums, never averaged."""

    _name = 'mos.production.report'
    _description = 'MOS production report'
    _auto = False
    _order = 'day desc'

    day = fields.Date(readonly=True)
    site_id = fields.Many2one('mos.site', readonly=True)
    shift = fields.Char(readonly=True)
    runs = fields.Integer(readonly=True, aggregator='sum')
    wheat_kg = fields.Float(readonly=True, aggregator='sum')
    flour_kg = fields.Float(readonly=True, aggregator='sum')
    bran_kg = fields.Float(readonly=True, aggregator='sum')
    loss_kg = fields.Float(readonly=True, aggregator='sum')
    extraction_pct = fields.Float(readonly=True, aggregator='avg', string='Extraction (%) of runs')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT r.id AS id, r.run_date AS day, r.site_id, r.shift, 1 AS runs, r.wheat_kg, r.flour_kg, r.bran_kg, r.loss_kg, r.extraction_pct
                  FROM mos_milling_run r WHERE r.state = 'done'
            )
            """
        )


class MosPlannerReport(models.Model):
    """For each product that is sold: what customers are waiting for, what is ready, what is held and what is still being made (FR-MFG-018)."""

    _name = 'mos.planner.report'
    _description = 'MOS planner demand and stock'
    _auto = False
    _order = 'shortfall desc'

    product_id = fields.Many2one('product.product', readonly=True)
    open_demand = fields.Float(string='Open sales orders', readonly=True)
    available = fields.Float(string='Available now', readonly=True)
    held = fields.Float(string='Held or blocked', readonly=True)
    shortfall = fields.Float(readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                WITH demand AS (
                    SELECT sol.product_id, SUM(GREATEST(sol.product_uom_qty - COALESCE(sol.qty_delivered, 0), 0)) AS qty
                      FROM sale_order_line sol JOIN sale_order so ON so.id = sol.order_id
                     WHERE so.state = 'sale' AND sol.product_id IS NOT NULL
                  GROUP BY sol.product_id
                ), stock AS (
                    SELECT sq.product_id,
                           SUM(CASE WHEN sl.mos_stock_status = 'available' THEN sq.quantity ELSE 0 END) AS available,
                           SUM(CASE WHEN sl.mos_stock_status <> 'available' THEN sq.quantity ELSE 0 END) AS held
                      FROM stock_quant sq JOIN stock_location sl ON sl.id = sq.location_id
                     WHERE sl.usage = 'internal'
                  GROUP BY sq.product_id
                )
                SELECT d.product_id AS id, d.product_id, d.qty AS open_demand, COALESCE(s.available, 0) AS available, COALESCE(s.held, 0) AS held,
                       GREATEST(d.qty - COALESCE(s.available, 0), 0) AS shortfall
                  FROM demand d LEFT JOIN stock s ON s.product_id = d.product_id
            )
            """
        )
