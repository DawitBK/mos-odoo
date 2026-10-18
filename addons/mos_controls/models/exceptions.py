from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

KINDS = [
    ('gate_incident', 'Open gate incident'),
    ('pass_overdue', 'Material pass not returned'),
    ('match_variance', 'Supplier invoice variance'),
    ('sample_due', 'Retention sample due for disposal'),
    ('document_expired', 'Supplier document expired'),
    ('approval_stuck', 'Approval waiting too long'),
    ('weigh_unverified', 'Weigh ticket awaiting verification'),
    ('long_stay', 'Vehicle on site too long'),
    ('lock_missing', 'Period not closed'),
]


class MosException(models.Model):
    """One thing that needs somebody's attention, found by the system or raised by a person (FR-PLT-050..053).

    The queue is the one place a manager looks: each row says what, where and how bad, who owns it and by when.
    The scan opens rows for what it finds and closes the ones whose cause has gone.
    """

    _name = 'mos.exception'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS process exception'
    _order = 'severity desc, due_date, id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    kind = fields.Selection(KINDS, required=True, tracking=True)
    severity = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='medium', required=True, tracking=True)
    summary = fields.Char(required=True)
    res_model = fields.Char(index=True)
    res_id = fields.Many2oneReference(model_field='res_model', index=True)
    site_id = fields.Many2one('mos.site')
    assignee_id = fields.Many2one('res.users', tracking=True)
    due_date = fields.Date()
    state = fields.Selection([('open', 'Open'), ('assigned', 'Assigned'), ('resolved', 'Resolved'), ('dismissed', 'Dismissed')], default='open', required=True, readonly=True, tracking=True)
    resolution = fields.Char(readonly=True)
    closed_at = fields.Datetime(readonly=True)
    overdue = fields.Boolean(compute='_compute_overdue', search='_search_overdue')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.exception')
        return super().create(vals_list)

    @api.depends('due_date', 'state')
    def _compute_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.overdue = rec.state in ('open', 'assigned') and bool(rec.due_date) and rec.due_date < today

    def _search_overdue(self, operator, value):
        today = fields.Date.context_today(self)
        domain = [('state', 'in', ('open', 'assigned')), ('due_date', '<', today)]
        if operator not in ('=', '!=', 'in', 'not in'):
            raise UserError(self.env._('Unsupported search on overdue.'))
        values = [value] if isinstance(value, bool) else list(value)
        positive = (operator in ('=', 'in')) == (True in values)
        return domain if positive else ['!', '&'] + domain

    @api.model
    def raise_for(self, kind, record, summary, severity='medium', due_days=2):
        """Open an exception for a record unless one is already open for it (the same kind and record)."""
        existing = self.sudo().search([('kind', '=', kind), ('res_model', '=', record._name), ('res_id', '=', record.id), ('state', 'in', ('open', 'assigned'))], limit=1)
        if existing:
            return existing
        site = record.site_id if 'site_id' in record._fields else self.env['mos.site']
        return self.sudo().create(
            {
                'kind': kind,
                'severity': severity,
                'summary': summary,
                'res_model': record._name,
                'res_id': record.id,
                'site_id': site.id or False,
                'due_date': fields.Date.context_today(self) + timedelta(days=due_days),
            }
        )

    def action_assign(self, user=None):
        for rec in self:
            if rec.state in ('resolved', 'dismissed'):
                raise UserError(self.env._('This exception is closed.'))
            rec.write({'assignee_id': (user or self.env.user).id, 'state': 'assigned'})

    def action_resolve(self, note=''):
        for rec in self:
            if rec.state in ('resolved', 'dismissed'):
                raise UserError(self.env._('This exception is closed.'))
            if not note:
                raise UserError(self.env._('Say what was done.'))
            rec.write({'state': 'resolved', 'resolution': note, 'closed_at': fields.Datetime.now()})

    def action_dismiss(self, reason=''):
        for rec in self:
            if rec.state in ('resolved', 'dismissed'):
                raise UserError(self.env._('This exception is closed.'))
            if not reason:
                raise UserError(self.env._('Say why it is not a problem.'))
            if rec.severity == 'high' and not self.env.user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('Only an administrator can dismiss a high-severity exception.'))
            rec.write({'state': 'dismissed', 'resolution': reason, 'closed_at': fields.Datetime.now()})

    def action_open_record(self):
        self.ensure_one()
        if not (self.res_model and self.res_id):
            raise UserError(self.env._('This exception is not linked to a record.'))
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'view_mode': 'form', 'target': 'current'}

    # -- the scan ---------------------------------------------------------------------------------------------------
    @api.model
    def _detectors(self):
        """(kind, severity, model, domain, summary text from a record). A record matching a domain gets one open exception."""
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        return [
            ('gate_incident', 'medium', 'mos.gate.incident', [('state', '!=', 'closed')], lambda r: f'{r.name}: {r.kind}'),
            ('pass_overdue', 'medium', 'mos.gate.pass', [('overdue', '=', True)], lambda r: f'{r.name} not returned, due {r.expected_return}'),
            ('match_variance', 'medium', 'mos.supplier.invoice', [('match_state', '=', 'exception'), ('state', '=', 'draft')], lambda r: f'{r.name or r.supplier_ref}: billed differs from received'),
            ('sample_due', 'low', 'mos.lab.sample', [('due', '=', True)], lambda r: f'{r.name} retained until {r.retain_until}'),
            ('document_expired', 'high', 'mos.supplier.document', [('expires_on', '<', today), ('partner_id.mos_supplier_state', '=', 'approved')], lambda r: f'{r.partner_id.display_name}: {r.doc_type} expired {r.expires_on}'),
            ('approval_stuck', 'medium', 'mos.approval.request', [('state', '=', 'pending'), ('submitted_at', '<', now - timedelta(hours=48))], lambda r: f'{r.name} ({r.doc_name}) pending since {r.submitted_at:%Y-%m-%d}'),
            ('weigh_unverified', 'medium', 'mos.weigh.ticket', [('state', '=', 'weighed'), ('create_date', '<', now - timedelta(hours=2))], lambda r: f'{r.name} waiting for a second person'),
            ('long_stay', 'medium', 'mos.gate.visit', [('state', 'in', ('arrived', 'weighing', 'weighed', 'cleared')), ('arrived_at', '<', now - timedelta(hours=12))], lambda r: f'{r.name} ({r.plate}) on site since {r.arrived_at:%Y-%m-%d %H:%M}'),
        ]

    @api.model
    def _cron_scan(self):
        """Open an exception for each finding, close the ones whose cause is gone. Returns (opened, closed)."""
        opened = closed = 0
        for kind, severity, model, domain, text in self._detectors():
            if model not in self.env:
                continue
            found = self.env[model].sudo().search(domain)
            seen = set(found.ids)
            for rec in found:
                before = self.sudo().search_count([('kind', '=', kind), ('res_model', '=', model), ('res_id', '=', rec.id), ('state', 'in', ('open', 'assigned'))])
                self.raise_for(kind, rec, text(rec), severity)
                opened += 0 if before else 1
            stale = self.sudo().search([('kind', '=', kind), ('res_model', '=', model), ('state', 'in', ('open', 'assigned'))]).filtered(lambda e: e.res_id not in seen)
            if stale:
                stale.sudo().write({'state': 'resolved', 'resolution': self.env._('Cleared by itself: the cause is gone.'), 'closed_at': fields.Datetime.now()})
                closed += len(stale)
        return opened, closed
