from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

AREAS = [
    ('payables', 'Payables (invoices, payments, vouchers, debit notes)'),
    ('receivables', 'Receivables (customer invoices, receipts)'),
    ('inventory', 'Inventory (adjustments)'),
    ('production', 'Production (milling runs)'),
]


class MosPeriodLock(models.Model):
    """Nothing may be posted into a closed period (FR-PLT-070..073). One row per area: its lock date.

    Moving the date forward closes more of the past and is the finance manager's act; moving it back reopens the
    past, which needs the general manager and a reason. Every change is audited with who and why.
    """

    _name = 'mos.period.lock'
    _inherit = ['mos.audited']
    _description = 'MOS period lock'
    _order = 'area'
    _rec_name = 'area'

    area = fields.Selection(AREAS, required=True)
    lock_date = fields.Date(readonly=True, help='Documents dated on or before this day can no longer be created or changed.')
    changed_by_id = fields.Many2one('res.users', readonly=True)
    change_reason = fields.Char(readonly=True)

    _area_uniq = models.Constraint('unique(area)', 'This area already has a lock row.')

    def set_lock(self, lock_date, reason=''):
        user = self.env.user
        for lock in self:
            if lock.lock_date and lock_date < lock.lock_date:
                if not (user.has_group('mos_approvals.group_mos_general_manager') or user.has_group('mos_core.group_mos_admin')):
                    raise UserError(self.env._('Only a general manager can reopen a closed period.'))
                if not reason:
                    raise UserError(self.env._('Give the reason for reopening the period.'))
            elif not (user.has_group('mos_approvals.group_mos_finance_manager') or user.has_group('mos_core.group_mos_admin')):
                raise UserError(self.env._('Only a finance manager can close a period.'))
            if lock_date > fields.Date.context_today(self):
                raise UserError(self.env._('A period cannot be closed in the future.'))
            old = lock.lock_date
            lock.sudo().write({'lock_date': lock_date, 'changed_by_id': user.id, 'change_reason': reason})
            self.env['mos.audit.event'].record('period_lock_changed', lock, f'{lock.area}: {old} -> {lock_date} {reason}')

    @api.model
    def lock_for(self, area):
        return self.sudo().search([('area', '=', area)], limit=1)

    @api.model
    def check(self, area, date):
        """Raise if the date falls in a closed period, is too far back or forward, or is plainly wrong."""
        if not date:
            return
        lock = self.lock_for(area)
        if lock.lock_date and date <= lock.lock_date:
            raise UserError(self.env._('The %(area)s period up to %(date)s is closed; a document cannot be dated %(doc)s.', area=area, date=lock.lock_date, doc=date))
        policy = self.env['mos.date.policy'].sudo().search([('area', '=', area)], limit=1)
        today = fields.Date.context_today(self)
        if policy:
            if date < today - timedelta(days=policy.max_back_days):
                raise UserError(self.env._('That date is more than %(n)s days in the past; the %(area)s backdating window is %(n)s days.', n=policy.max_back_days, area=area))
            if date > today + timedelta(days=policy.max_forward_days):
                raise UserError(self.env._('That date is more than %(n)s days ahead; the %(area)s window allows %(n)s.', n=policy.max_forward_days, area=area))
        if date.year < 2000 or date.year > today.year + 1:
            raise UserError(self.env._('%s is not a plausible date for a document.', date))


class MosDatePolicy(models.Model):
    """How far back and forward a document may be dated, per area (FR-PLT-074)."""

    _name = 'mos.date.policy'
    _inherit = ['mos.audited']
    _description = 'MOS date window'
    _rec_name = 'area'

    area = fields.Selection(AREAS, required=True)
    max_back_days = fields.Integer(string='Days back', default=7, required=True)
    max_forward_days = fields.Integer(string='Days ahead', default=1, required=True)

    _area_uniq = models.Constraint('unique(area)', 'This area already has a date window.')

    @api.constrains('max_back_days', 'max_forward_days')
    def _check(self):
        for policy in self:
            if policy.max_back_days < 0 or policy.max_forward_days < 0:
                raise ValidationError(self.env._('A window cannot be negative.'))


class MosDateGuard(models.AbstractModel):
    """Add to a model with ``_mos_guard = (area, date_field)`` and its documents obey the lock and the date window."""

    _name = 'mos.date.guard'
    _description = 'Document date guard'

    _mos_guard = (None, None)

    def _mos_check_dates(self):
        area, field = self._mos_guard
        for rec in self:
            self.env['mos.period.lock'].check(area, rec[field])

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._mos_check_dates()
        return records

    def write(self, vals):
        _area, field = self._mos_guard
        if field in vals:
            area = self._mos_guard[0]
            new = fields.Date.to_date(vals[field])
            for rec in self:
                self.env['mos.period.lock'].check(area, new)
                # moving a document out of a closed period is as bad as moving one in
                if rec[field]:
                    lock = self.env['mos.period.lock'].lock_for(area)
                    if lock.lock_date and rec[field] <= lock.lock_date:
                        raise UserError(self.env._('This document is dated in a closed period and cannot be redated.'))
        return super().write(vals)


class MosSupplierInvoiceGuard(models.Model):
    _name = 'mos.supplier.invoice'
    _inherit = ['mos.supplier.invoice', 'mos.date.guard']
    _mos_guard = ('payables', 'invoice_date')


class MosPaymentGuard(models.Model):
    _name = 'mos.payment'
    _inherit = ['mos.payment', 'mos.date.guard']
    _mos_guard = ('payables', 'date')


class MosVoucherGuard(models.Model):
    _name = 'mos.payment.voucher'
    _inherit = ['mos.payment.voucher', 'mos.date.guard']
    _mos_guard = ('payables', 'date')


class MosDebitNoteGuard(models.Model):
    _name = 'mos.debit.note'
    _inherit = ['mos.debit.note', 'mos.date.guard']
    _mos_guard = ('payables', 'date')


class MosCustomerInvoiceGuard(models.Model):
    _name = 'mos.customer.invoice'
    _inherit = ['mos.customer.invoice', 'mos.date.guard']
    _mos_guard = ('receivables', 'date')


class MosReceiptGuard(models.Model):
    _name = 'mos.receipt'
    _inherit = ['mos.receipt', 'mos.date.guard']
    _mos_guard = ('receivables', 'date')


class MosAdjustmentGuard(models.Model):
    _name = 'mos.stock.adjustment'
    _inherit = ['mos.stock.adjustment', 'mos.date.guard']
    _mos_guard = ('inventory', 'date')


class MosMillingRunGuard(models.Model):
    _name = 'mos.milling.run'
    _inherit = ['mos.milling.run', 'mos.date.guard']
    _mos_guard = ('production', 'run_date')


class MosPeriodLockWizard(models.TransientModel):
    _name = 'mos.period.lock.wizard'
    _description = 'MOS close or reopen a period'

    lock_id = fields.Many2one('mos.period.lock', required=True, readonly=True)
    area = fields.Selection(related='lock_id.area')
    current = fields.Date(related='lock_id.lock_date', string='Closed up to now')
    lock_date = fields.Date(string='Close up to and including', required=True)
    reason = fields.Char(help='Required when the date moves back.')

    def action_confirm(self):
        self.ensure_one()
        self.lock_id.set_lock(self.lock_date, self.reason or '')
        return {'type': 'ir.actions.act_window_close'}


class MosPeriodLockActions(models.Model):
    _inherit = 'mos.period.lock'

    def action_open_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Close or reopen the period'),
            'res_model': 'mos.period.lock.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_lock_id': self.id, 'default_lock_date': self.lock_date or fields.Date.context_today(self)},
        }
