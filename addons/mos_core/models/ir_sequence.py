import calendar
from datetime import date, datetime, timedelta

from odoo import api, fields, models


class IrSequence(models.Model):
    """Number series with a restart policy and a fiscal-year token (FR-PLT-040).

    The pattern is the sequence's prefix, written with Odoo's tokens (%(range_year)s, %(month)s, %(day)s ...) and one more: %(fy)s, the fiscal
    year of the document's date (2026 when the fiscal year is the calendar year, 2026-27 when it runs across two). The counter restarts
    never, each fiscal year, each calendar year or each month: the policy decides how the date ranges of the sequence are cut.
    """

    _inherit = 'ir.sequence'

    mos_reset = fields.Selection(
        [('never', 'Never'), ('fiscal', 'Each fiscal year'), ('calendar', 'Each calendar year'), ('month', 'Each month')],
        string='Restart numbering',
        default='calendar',
        required=True,
        help='When the counter goes back to 1. Each choice but Never numbers by date range.',
    )

    @api.onchange('mos_reset')
    def _onchange_mos_reset(self):
        self.use_date_range = self.mos_reset != 'never'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'mos_reset' in vals:
                vals['use_date_range'] = vals['mos_reset'] != 'never'
            else:
                # A sequence declared the old way: yearly ranges are the calendar policy, none is never.
                vals['mos_reset'] = 'calendar' if vals.get('use_date_range') else 'never'
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('mos_reset') == 'never':
            vals = dict(vals, use_date_range=False)
        elif vals.get('mos_reset') in ('fiscal', 'calendar', 'month'):
            vals = dict(vals, use_date_range=True)
        return super().write(vals)

    # -- the fiscal year ------------------------------------------------------------------------------------------------
    def _mos_fiscal_end(self, day):
        """The last day of the fiscal year that `day` falls in, from the company's fiscal-year end."""
        company = self.company_id or self.env.company
        month = int(getattr(company, 'fiscalyear_last_month', 12) or 12)
        last = int(getattr(company, 'fiscalyear_last_day', 31) or 31)
        end = date(day.year, month, min(last, calendar.monthrange(day.year, month)[1]))
        if day > end:
            end = date(day.year + 1, month, min(last, calendar.monthrange(day.year + 1, month)[1]))
        return end

    def _mos_fiscal_label(self, day):
        end = self._mos_fiscal_end(day)
        start = self._mos_fiscal_end(date(end.year - 1, end.month, 1)) + timedelta(days=1)
        return str(end.year) if start.year == end.year else f'{start.year}-{str(end.year)[2:]}'

    def _mos_range_for(self, day):
        """The (from, to) dates of the range that `day` belongs to under this sequence's restart policy."""
        if self.mos_reset == 'month':
            return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
        if self.mos_reset == 'fiscal':
            end = self._mos_fiscal_end(day)
            previous_end = self._mos_fiscal_end(date(end.year - 1, end.month, 1))
            return previous_end + timedelta(days=1), end
        return date(day.year, 1, 1), date(day.year, 12, 31)

    def _create_date_range_seq(self, date_):
        if self.mos_reset not in ('month', 'fiscal'):
            return super()._create_date_range_seq(date_)
        day = fields.Date.to_date(date_)
        date_from, date_to = self._mos_range_for(day)
        return self.env['ir.sequence.date_range'].sudo().create({'date_from': date_from, 'date_to': date_to, 'sequence_id': self.id})

    # -- the %(fy)s token -----------------------------------------------------------------------------------------------
    def _get_prefix_suffix(self, date=None, date_range=None):
        self.ensure_one()
        if '%(fy)s' not in f'{self.prefix or ""}{self.suffix or ""}':
            return super()._get_prefix_suffix(date=date, date_range=date_range)
        # Odoo interpolates with a fixed set of keys: put the fiscal year in first, on an unsaved copy, and let Odoo do the rest.
        when = fields.Datetime.from_string(date or self.env.context.get('ir_sequence_date') or fields.Datetime.now())
        label = self._mos_fiscal_label(when.date() if isinstance(when, datetime) else when)
        copy = self.new({'prefix': (self.prefix or '').replace('%(fy)s', label), 'suffix': (self.suffix or '').replace('%(fy)s', label), 'company_id': self.company_id.id})
        return copy._get_prefix_suffix(date=date, date_range=date_range)
