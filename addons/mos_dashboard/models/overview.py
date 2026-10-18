from datetime import timedelta

from odoo import api, fields, models

OPEN_VISIT_STATES = ('arrived', 'weighing', 'weighed', 'cleared')


class MosOverview(models.TransientModel):
    """The landing screen. Every number is read live when it is opened; nothing is stored."""

    _name = 'mos.overview'
    _description = 'MOS overview'

    title = fields.Char(compute='_compute_figures')
    # Gate and lab
    trucks_on_site = fields.Integer(compute='_compute_figures')
    inspections_open = fields.Integer(compute='_compute_figures')
    received_today_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    received_month_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    rejected_month = fields.Integer(compute='_compute_figures')
    rejection_rate = fields.Float(digits=(5, 1), compute='_compute_figures')
    # Silos
    silo_level_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    silo_capacity_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    silo_fill_pct = fields.Float(digits=(5, 1), compute='_compute_figures')
    silo_ids = fields.Many2many('mos.silo', compute='_compute_figures')
    # Milling
    planned_runs = fields.Integer(compute='_compute_figures')
    wheat_today_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    flour_today_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    extraction_30d = fields.Float(digits=(5, 2), compute='_compute_figures')
    flour_stock_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    run_ids = fields.Many2many('mos.milling.run', compute='_compute_figures')
    # Money and approvals
    payable_total = fields.Float(digits=(14, 0), compute='_compute_figures', string='We owe suppliers')
    receivable_total = fields.Float(digits=(14, 0), compute='_compute_figures', string='Customers owe us')
    sales_month_kg = fields.Float(digits=(14, 0), compute='_compute_figures')
    sales_month_amount = fields.Float(digits=(14, 0), compute='_compute_figures')
    approvals_waiting = fields.Integer(compute='_compute_figures')

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = self.env._('Overview')

    @api.model
    def action_open(self):
        record = self.create({})
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Overview'),
            'res_model': 'mos.overview',
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _compute_figures(self):
        site = self.env.user.mos_site_ids[:1] or self.env['mos.site'].search([], limit=1)
        # "Today" is the date at the mill, not in Greenwich (FR-PLT-004).
        today = site.business_date() if site else fields.Date.context_today(self)
        month_start = today.replace(day=1)
        visits = self.env['mos.gate.visit']
        inspections = self.env['mos.lab.inspection']
        runs = self.env['mos.milling.run']
        silos = self.env['mos.silo'].search([]).fresh()
        recent_runs = runs.search([], limit=8)
        done_runs = runs.search([('state', '=', 'done'), ('run_date', '>=', today - timedelta(days=30))])
        today_runs = done_runs.filtered(lambda r: r.run_date == today)

        exited = visits.search([('state', '=', 'exited'), ('exited_at', '>=', fields.Datetime.to_datetime(month_start))])
        confirmed = exited.filtered(lambda v: v.grn_state == 'confirmed')
        rejected = exited.filtered(lambda v: v.grn_state == 'rejected')
        now = fields.Datetime.now()
        day_start = fields.Datetime.to_datetime(today)
        received_today = confirmed.filtered(lambda v: v.exited_at and v.exited_at >= day_start)

        flour_products = self.env['product.product'].sudo().search([('categ_id.name', 'in', ['Flour', 'Semolina'])])
        flour_stock = sum(flour_products.mapped('qty_available')) if flour_products else 0.0
        wheat_30 = sum(done_runs.mapped('wheat_kg'))
        flour_30 = sum(done_runs.mapped('flour_kg'))
        capacity = sum(silos.mapped('capacity_kg'))
        level = sum(silos.mapped('level_kg'))

        supplier_rows = self.env['mos.supplier.balance'].sudo().search([])
        customer_rows = self.env['mos.customer.balance'].sudo().search([])
        month_invoices = self.env['mos.customer.invoice'].search([('date', '>=', month_start)])
        waiting = self.env['mos.approval.step'].search_count([('state', '=', 'pending'), ('eligible_user_ids', 'in', self.env.uid)])
        for rec in self:
            rec.payable_total = sum(supplier_rows.mapped('balance'))
            rec.receivable_total = sum(customer_rows.mapped('balance'))
            rec.sales_month_kg = sum(month_invoices.mapped('qty'))
            rec.sales_month_amount = sum(month_invoices.mapped('gross_amount'))
            rec.approvals_waiting = waiting
            rec.title = self.env._('%(company)s: operations as of %(time)s', company=self.env.company.name, time=fields.Datetime.context_timestamp(rec, now).strftime('%d %b %H:%M'))
            rec.trucks_on_site = visits.search_count([('state', 'in', OPEN_VISIT_STATES)])
            rec.inspections_open = inspections.search_count([('state', '!=', 'decided')])
            rec.received_today_kg = sum(received_today.mapped('inspection_id.payable_qty'))
            rec.received_month_kg = sum(confirmed.mapped('inspection_id.payable_qty'))
            rec.rejected_month = len(rejected)
            total_loads = len(confirmed) + len(rejected)
            rec.rejection_rate = (len(rejected) / total_loads * 100.0) if total_loads else 0.0
            rec.silo_level_kg = level
            rec.silo_capacity_kg = capacity
            rec.silo_fill_pct = (level / capacity * 100.0) if capacity else 0.0
            rec.silo_ids = silos
            rec.planned_runs = runs.search_count([('state', '=', 'draft')])
            rec.wheat_today_kg = sum(today_runs.mapped('wheat_kg'))
            rec.flour_today_kg = sum(today_runs.mapped('flour_kg'))
            rec.extraction_30d = (flour_30 / wheat_30 * 100.0) if wheat_30 else 0.0
            rec.flour_stock_kg = flour_stock
            rec.run_ids = recent_runs

    def action_open_trucks(self):
        return self._open('mos.gate.visit', self.env._('Trucks on site'), [('state', 'in', OPEN_VISIT_STATES)])

    def action_open_inspections(self):
        return self._open('mos.lab.inspection', self.env._('Inspections waiting for a decision'), [('state', '!=', 'decided')])

    def action_open_planned_runs(self):
        return self._open('mos.milling.run', self.env._('Planned milling runs'), [('state', '=', 'draft')])

    def action_open_approvals(self):
        return self.env['ir.actions.actions']._for_xml_id('mos_approvals.action_mos_approval_inbox')

    def action_open_silos(self):
        return self._open('mos.silo', self.env._('Silos'), [], 'kanban,list,form')

    def _open(self, model, name, domain, modes='list,form'):
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'view_mode': modes, 'domain': domain}
