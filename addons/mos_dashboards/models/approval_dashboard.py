from datetime import date, datetime, time

from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import api, fields, models


class MosApprovalDashboard(models.TransientModel):
    """Approvals at a glance for one kind of document (purchase requests, purchase orders, invoices...) and a period: how many were
    submitted, how many wait, were approved, rejected or returned, how long an approval takes on average, and the same by month for the
    last six months (FR-PLT-030, FR-RPT-001). The figures cover the approvals the person may see, so a requester sees their own, an
    approver theirs, and an administrator all of them. Every figure opens the list behind it."""

    _name = 'mos.approval.dashboard'
    _description = 'MOS approvals dashboard'

    res_model = fields.Selection(selection='_doc_models', string='Document', help='Empty means every kind of document.')
    date_from = fields.Date(required=True, default=lambda s: fields.Date.context_today(s) - relativedelta(months=2, day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    site_id = fields.Many2one('mos.site', string='Site')

    total = fields.Integer(compute='_compute_figures')
    pending = fields.Integer(compute='_compute_figures')
    approved = fields.Integer(compute='_compute_figures')
    rejected = fields.Integer(compute='_compute_figures')
    returned = fields.Integer(compute='_compute_figures')
    overdue = fields.Integer(compute='_compute_figures', help='Waiting longer than the time limit of the current stage.')
    avg_hours = fields.Float(string='Average approval time (hours)', compute='_compute_figures', digits=(10, 1))
    trend_html = fields.Html(compute='_compute_figures', sanitize=False)

    def _compute_display_name(self):
        for dash in self:
            dash.display_name = self.env._('Approvals dashboard')

    @api.model
    def _doc_models(self):
        """The kinds of document that have approvals, with the name people know them by."""
        self.env.cr.execute('SELECT DISTINCT res_model FROM mos_approval_request ORDER BY res_model')
        names = {m.model: m.name for m in self.env['ir.model'].sudo().search([('model', 'in', [r[0] for r in self.env.cr.fetchall()])])}
        return sorted(names.items(), key=lambda item: item[1])

    # -- figures --------------------------------------------------------------------------------------------------------------
    def _domain(self, start=None, end=None):
        self.ensure_one()
        start = datetime.combine(start or self.date_from, time.min)
        end = datetime.combine((end or self.date_to) + relativedelta(days=1), time.min)
        domain = [('submitted_at', '>=', start), ('submitted_at', '<', end)]
        if self.res_model:
            domain.append(('res_model', '=', self.res_model))
        if self.site_id:
            domain.append(('site_id', '=', self.site_id.id))
        return domain

    @staticmethod
    def _average_hours(requests):
        spans = [(r.closed_at - r.submitted_at).total_seconds() / 3600.0 for r in requests if r.state == 'approved' and r.closed_at and r.submitted_at]
        return round(sum(spans) / len(spans), 1) if spans else 0.0

    @api.depends('res_model', 'date_from', 'date_to', 'site_id')
    def _compute_figures(self):
        requests = self.env['mos.approval.request']
        for dash in self:
            if not (dash.date_from and dash.date_to):
                dash.total = dash.pending = dash.approved = dash.rejected = dash.returned = dash.overdue = 0
                dash.avg_hours = 0.0
                dash.trend_html = False
                continue
            found = requests.search(dash._domain())
            dash.total = len(found)
            dash.pending = len(found.filtered(lambda r: r.state == 'pending'))
            dash.approved = len(found.filtered(lambda r: r.state == 'approved'))
            dash.rejected = len(found.filtered(lambda r: r.state == 'rejected'))
            dash.returned = len(found.filtered(lambda r: r.state == 'returned'))
            dash.overdue = dash._overdue(found)
            dash.avg_hours = dash._average_hours(found)
            dash.trend_html = dash._trend()

    def _overdue(self, found):
        now = fields.Datetime.now()
        waiting = found.filtered(lambda r: r.state == 'pending')
        return len(waiting.filtered(lambda r: any(s.state == 'pending' and s.due_at and s.due_at < now for s in r.sudo().step_ids)))

    def _trend(self):
        """The last six months up to the end date: submitted, approved, rejected and the average approval time, as a table with bars."""
        self.ensure_one()
        first = self.date_to.replace(day=1) - relativedelta(months=5)
        rows = []
        for step in range(6):
            start = first + relativedelta(months=step)
            end = start + relativedelta(months=1, days=-1)
            found = self.env['mos.approval.request'].search(self._domain(start, end))
            rows.append(
                (
                    start.strftime('%b %Y'),
                    len(found),
                    len(found.filtered(lambda r: r.state == 'approved')),
                    len(found.filtered(lambda r: r.state in ('rejected', 'returned'))),
                    self._average_hours(found),
                )
            )
        longest = max((row[4] for row in rows), default=0.0) or 1.0
        head = Markup('<thead><tr><th>Month</th><th class="text-end">Submitted</th><th class="text-end">Approved</th><th class="text-end">Rejected or returned</th><th class="text-end">Average hours</th><th style="width:30%"></th></tr></thead>')
        body = Markup('').join(
            Markup('<tr><td>{}</td><td class="text-end">{}</td><td class="text-end">{}</td><td class="text-end">{}</td><td class="text-end">{}</td><td><div class="mos-bar" style="width:{}%"></div></td></tr>').format(
                escape(label), submitted, approved, rejected, f'{hours:.1f}', round(hours / longest * 100)
            )
            for label, submitted, approved, rejected, hours in rows
        )
        return Markup('<table class="table table-sm mos-trend">{}<tbody>{}</tbody></table>').format(head, body)

    # -- drill down -----------------------------------------------------------------------------------------------------------
    def _open(self, title, extra=None):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'mos.approval.request',
            'view_mode': 'list,form',
            'domain': self._domain() + (extra or []),
        }

    def action_open_total(self):
        return self._open(self.env._('Approvals submitted'))

    def action_open_pending(self):
        return self._open(self.env._('Waiting for approval'), [('state', '=', 'pending')])

    def action_open_approved(self):
        return self._open(self.env._('Approved'), [('state', '=', 'approved')])

    def action_open_rejected(self):
        return self._open(self.env._('Rejected'), [('state', '=', 'rejected')])

    def action_open_returned(self):
        return self._open(self.env._('Returned for changes'), [('state', '=', 'returned')])

    def action_open_overdue(self):
        now = fields.Datetime.now()
        late = self.env['mos.approval.request'].search(self._domain() + [('state', '=', 'pending')]).filtered(
            lambda r: any(s.state == 'pending' and s.due_at and s.due_at < now for s in r.sudo().step_ids)
        )
        return self._open(self.env._('Waiting longer than the time limit'), [('id', 'in', late.ids)])
