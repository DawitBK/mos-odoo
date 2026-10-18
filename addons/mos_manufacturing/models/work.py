from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError


class MosReason(models.Model):
    _inherit = 'mos.reason'

    domain = fields.Selection(selection_add=[('downtime', 'Production downtime')], ondelete={'downtime': 'cascade'})


class MosWorkCentre(models.Model):
    """A place where work happens: cleaning, a milling line, packing (FR-MFG-001)."""

    _name = 'mos.work.centre'
    _inherit = ['mos.audited']
    _description = 'MOS work centre'
    _order = 'code'

    name = fields.Char(required=True)
    code = fields.Char(required=True, size=12)
    site_id = fields.Many2one('mos.site', required=True)
    kind = fields.Selection([('cleaning', 'Cleaning'), ('milling', 'Milling'), ('packing', 'Packing'), ('other', 'Other')], default='milling', required=True)
    capacity_kg_h = fields.Float(string='Capacity (kg/hour)', digits=(12, 0))
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(code, site_id)', 'The code must be unique per site.')


class MosDowntime(models.Model):
    """A stop of a work centre, with its reason. Stops of one centre never overlap (FR-MFG-012)."""

    _name = 'mos.downtime'
    _inherit = ['mos.audited']
    _description = 'MOS downtime'
    _order = 'start desc'

    work_centre_id = fields.Many2one('mos.work.centre', required=True)
    site_id = fields.Many2one(related='work_centre_id.site_id', store=True)
    start = fields.Datetime(required=True, default=fields.Datetime.now)
    end = fields.Datetime()
    reason_id = fields.Many2one('mos.reason', required=True, domain=[('domain', '=', 'downtime')])
    planned = fields.Boolean(string='Planned stop')
    note = fields.Char()
    minutes = fields.Float(compute='_compute_minutes', store=True, digits=(12, 1))

    @api.depends('start', 'end')
    def _compute_minutes(self):
        for rec in self:
            rec.minutes = (rec.end - rec.start).total_seconds() / 60.0 if rec.start and rec.end else 0.0

    @api.constrains('start', 'end', 'work_centre_id')
    def _check_overlap(self):
        for rec in self:
            if rec.end and rec.end <= rec.start:
                raise ValidationError(self.env._('A stop must end after it starts.'))
            far = fields.Datetime.from_string('9999-12-31 00:00:00')
            others = self.sudo().search([('work_centre_id', '=', rec.work_centre_id.id), ('id', '!=', rec.id)])
            for other in others:
                if rec.start < (other.end or far) and (rec.end or far) > other.start:
                    raise ValidationError(self.env._('This stop overlaps another stop of %s.', rec.work_centre_id.name))

    def action_end_now(self):
        for rec in self:
            if rec.end:
                raise UserError(self.env._('This stop has already ended.'))
            rec.end = fields.Datetime.now()


class MosShiftLog(models.Model):
    """What happened on a shift at a work centre, written by the supervisor for the next one (FR-MFG-012)."""

    _name = 'mos.shift.log'
    _inherit = ['mos.audited']
    _description = 'MOS shift log'
    _order = 'date desc, id desc'

    work_centre_id = fields.Many2one('mos.work.centre', required=True)
    site_id = fields.Many2one(related='work_centre_id.site_id', store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    shift = fields.Selection([('day', 'Day'), ('night', 'Night')], required=True, default='day')
    supervisor_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    notes = fields.Text(required=True)
    handover = fields.Text(help='What the next shift must know.')

    _shift_once = models.Constraint('unique(work_centre_id, date, shift)', 'There is already a log for this shift.')


class MosDowntimeReport(models.Model):
    """Lost minutes by work centre and reason."""

    _name = 'mos.downtime.report'
    _description = 'MOS downtime report'
    _auto = False

    work_centre_id = fields.Many2one('mos.work.centre', readonly=True)
    reason_id = fields.Many2one('mos.reason', readonly=True)
    day = fields.Date(readonly=True)
    planned = fields.Boolean(readonly=True)
    stops = fields.Integer(readonly=True, aggregator='sum')
    minutes = fields.Float(readonly=True, aggregator='sum')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
                SELECT d.id AS id, d.work_centre_id, d.reason_id, d.start::date AS day, d.planned, 1 AS stops, COALESCE(d.minutes, 0) AS minutes
                  FROM mos_downtime d
            )
            """
        )
