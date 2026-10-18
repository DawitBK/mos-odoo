from odoo import api, fields, models
from odoo.exceptions import UserError


class MosPeriodLock(models.Model):
    """A period can be soft-closed before it is closed: ordinary users can no longer post into it, a finance manager still can,
    for the late entries that always turn up (FR-PLT-042). Closing for good is the existing lock date."""

    _inherit = 'mos.period.lock'

    soft_lock_date = fields.Date(readonly=True, help='Documents dated on or before this day need a finance manager to post; after the lock date nobody can.')

    def set_soft_lock(self, soft_date, reason=''):
        user = self.env.user
        if not (user.has_group('mos_approvals.group_mos_finance_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a finance manager can soft-close a period.'))
        if soft_date and soft_date > fields.Date.context_today(self):
            raise UserError(self.env._('A period cannot be soft-closed in the future.'))
        for lock in self:
            if soft_date and lock.lock_date and soft_date < lock.lock_date:
                raise UserError(self.env._('The soft-close date cannot be before the closing date (%s).', lock.lock_date))
            old = lock.soft_lock_date
            lock.sudo().write({'soft_lock_date': soft_date, 'changed_by_id': user.id, 'change_reason': reason})
            self.env['mos.audit.event'].record('period_soft_lock_changed', lock, f'{lock.area}: {old} -> {soft_date} {reason}')

    def _mos_may_post_soft_closed(self):
        user = self.env.user
        return user.has_group('mos_approvals.group_mos_finance_manager') or user.has_group('mos_core.group_mos_admin')

    @api.model
    def check(self, area, date):
        super().check(area, date)
        if not date:
            return
        lock = self.lock_for(area)
        if lock.soft_lock_date and date <= lock.soft_lock_date and not lock._mos_may_post_soft_closed():
            raise UserError(self.env._('The %(area)s period up to %(date)s is soft-closed: a finance manager must post a document dated %(doc)s.', area=area, date=lock.soft_lock_date, doc=date))
