from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosLabInstrument(models.Model):
    """A measuring instrument with its calibration schedule. A result cannot be entered with an instrument that is overdue
    or out of service (FR-QC-020)."""

    _name = 'mos.lab.instrument'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS lab instrument'
    _order = 'code'

    code = fields.Char(required=True)
    name = fields.Char(required=True)
    kind = fields.Selection([('balance', 'Balance'), ('moisture', 'Moisture meter'), ('oven', 'Oven'), ('mill', 'Laboratory mill'), ('other', 'Other')], default='other', required=True)
    interval_days = fields.Integer(string='Calibrate every (days)', default=180, required=True)
    last_calibrated = fields.Date(compute='_compute_calibration', store=True)
    next_due = fields.Date(compute='_compute_calibration', store=True)
    in_service = fields.Boolean(compute='_compute_calibration', store=True)
    state = fields.Selection([('ok', 'In calibration'), ('due_soon', 'Due soon'), ('overdue', 'Overdue'), ('out_of_service', 'Out of service'), ('never', 'Never calibrated')], compute='_compute_state')
    calibration_ids = fields.One2many('mos.lab.calibration', 'instrument_id')
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('unique(code)', 'This instrument code already exists.')

    @api.constrains('interval_days')
    def _check_interval(self):
        for rec in self:
            if rec.interval_days < 1:
                raise ValidationError(self.env._('The calibration interval must be at least one day.'))

    @api.depends('calibration_ids.state', 'calibration_ids.date', 'calibration_ids.result', 'interval_days')
    def _compute_calibration(self):
        for inst in self:
            done = inst.calibration_ids.filtered(lambda c: c.state == 'posted').sorted(lambda c: (c.date, c.id))
            last = done[-1:] if done else done
            inst.in_service = bool(last) and last.result == 'pass'
            passed = done.filtered(lambda c: c.result == 'pass')
            inst.last_calibrated = passed[-1].date if passed else False
            inst.next_due = (passed[-1].date + timedelta(days=inst.interval_days)) if passed else False

    @api.depends('last_calibrated', 'next_due', 'in_service')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for inst in self:
            if not inst.last_calibrated and not inst.calibration_ids:
                inst.state = 'never'
            elif not inst.in_service:
                inst.state = 'out_of_service'
            elif inst.next_due and inst.next_due < today:
                inst.state = 'overdue'
            elif inst.next_due and inst.next_due <= today + timedelta(days=14):
                inst.state = 'due_soon'
            else:
                inst.state = 'ok'

    def check_usable(self):
        for inst in self:
            inst.invalidate_recordset(['state'])
            if inst.state in ('never', 'overdue', 'out_of_service'):
                raise UserError(self.env._('Instrument %(code)s is %(state)s and cannot be used for results.', code=inst.code, state=dict(inst._fields['state'].selection)[inst.state].lower()))


class MosLabCalibration(models.Model):
    _name = 'mos.lab.calibration'
    _inherit = ['mos.posted']
    _description = 'MOS calibration record'
    _order = 'date desc, id desc'
    _mos_final_states = ('posted',)

    instrument_id = fields.Many2one('mos.lab.instrument', required=True, ondelete='restrict')
    date = fields.Date(required=True, default=fields.Date.context_today)
    result = fields.Selection([('pass', 'Passed'), ('fail', 'Failed')], required=True)
    certificate_ref = fields.Char(string='Certificate number')
    by = fields.Char(string='Calibrated by (person or laboratory)')
    note = fields.Char()
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        if not (user.has_group('mos_lab.group_mos_lab_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a lab manager can record a calibration.'))
        return super().create(vals_list)


class MosLabResultInstrument(models.Model):
    _inherit = 'mos.lab.result'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('instrument_id'):
                self.env['mos.lab.instrument'].browse(vals['instrument_id']).check_usable()
        return super().create(vals_list)
