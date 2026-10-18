import base64
import csv
import io
import logging

from odoo import api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

REPORTS = [('stock_movements', 'Stock movements (every finished move)'), ('receipt_valuation', 'Receipt valuation'), ('audit_trail', 'Audit trail')]


class MosReportJob(models.Model):
    """A report too big to wait for: it is queued, built by a scheduled job, and the person who asked is told when it is ready. The
    page never blocks, however many months it covers (FR-RPT-003)."""

    _name = 'mos.report.job'
    _inherit = ['mail.thread']
    _description = 'MOS background report'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    report = fields.Selection(REPORTS, required=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([('queued', 'Queued'), ('running', 'Running'), ('done', 'Ready'), ('failed', 'Failed')], default='queued', readonly=True, tracking=True)
    rows = fields.Integer(readonly=True)
    file = fields.Binary(readonly=True, attachment=True)
    filename = fields.Char(readonly=True)
    error = fields.Text(readonly=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.report.job')
            if vals.get('date_to') and vals.get('date_from') and fields.Date.to_date(vals['date_to']) < fields.Date.to_date(vals['date_from']):
                raise UserError(self.env._('The end date is before the start date.'))
        return super().create(vals_list)

    # -- what each report produces -----------------------------------------------------------------------------------------------
    def _data(self):
        """(header, rows) for the job's report; rows are built in SQL so a year of movements stays fast."""
        self.ensure_one()
        cr = self.env.cr
        params = {'a': self.date_from, 'b': self.date_to}
        self.env.flush_all()
        if self.report == 'stock_movements':
            cr.execute(
                """
                SELECT h.date, pp.default_code, COALESCE(pt.name ->> 'en_US', pt.name::text), h.reference, h.kind, h.received, h.issued, h.adjustment, h.balance_after
                  FROM mos_stock_history h
                  JOIN product_product pp ON pp.id = h.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 WHERE h.date::date BETWEEN %(a)s AND %(b)s
              ORDER BY h.date, h.id
                """,
                params,
            )
            return ['Date', 'Code', 'Item', 'Reference', 'Kind', 'Received', 'Issued', 'Adjusted', 'Balance after'], cr.fetchall()
        if self.report == 'receipt_valuation':
            cr.execute(
                """
                SELECT v.date, po.name, v.qty, v.payable, v.unit_price, v.value, v.unit_cost
                  FROM mos_receipt_valuation v JOIN purchase_order po ON po.id = v.order_id
                 WHERE v.date BETWEEN %(a)s AND %(b)s ORDER BY v.date, v.id
                """,
                params,
            )
            return ['Date', 'Order', 'Received', 'Payable', 'Quality-adjusted price', 'Value', 'Unit cost'], cr.fetchall()
        cr.execute(
            """
            SELECT e.id, e.occurred_at, e.action, e.summary
              FROM mos_audit_event e WHERE e.occurred_at::date BETWEEN %(a)s AND %(b)s ORDER BY e.id
            """,
            params,
        )
        return ['Number', 'When', 'Event', 'Summary'], cr.fetchall()

    def _build(self):
        self.ensure_one()
        header, rows = self._data()
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(header)
        writer.writerows(rows)
        return buffer.getvalue(), len(rows)

    def action_run(self):
        """Build one queued job now."""
        for job in self:
            if job.state not in ('queued', 'failed'):
                raise UserError(self.env._('Only a queued or failed report can be run.'))
            job.sudo().write({'state': 'running', 'started_at': fields.Datetime.now(), 'error': False})
            try:
                with self.env.cr.savepoint():
                    text, count = job._build()
                job.sudo().write(
                    {
                        'state': 'done',
                        'rows': count,
                        'file': base64.b64encode(text.encode()).decode(),
                        'filename': f'{job.name}-{job.report}-{job.date_from}-{job.date_to}.csv',
                        'finished_at': fields.Datetime.now(),
                    }
                )
                job.message_notify(
                    partner_ids=job.requested_by_id.partner_id.ids,
                    subject=self.env._('Your report %s is ready', job.name),
                    body=self.env._('%(report)s from %(a)s to %(b)s has %(n)s rows. Open the report job to download it.', report=dict(REPORTS)[job.report], a=job.date_from, b=job.date_to, n=count),
                )
            except Exception as error:  # a failed report is recorded and the person told; it must not stop the queue
                _logger.exception('report job %s failed', job.name)
                job.sudo().write({'state': 'failed', 'error': str(error), 'finished_at': fields.Datetime.now()})
                job.message_notify(partner_ids=job.requested_by_id.partner_id.ids, subject=self.env._('Your report %s failed', job.name), body=str(error))

    @api.model
    def _cron_process(self, limit=5):
        jobs = self.sudo().search([('state', '=', 'queued')], order='id', limit=limit)
        for job in jobs:
            job.action_run()
            if not tools.config.get('test_enable'):
                self.env.cr.commit()
        return len(jobs)
