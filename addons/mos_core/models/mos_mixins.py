from odoo import api, models
from odoo.exceptions import UserError


class MosAudited(models.AbstractModel):
    """Writes an audit event for every create, change and delete of the inheriting model."""

    _name = 'mos.audited'
    _description = 'MOS audited record'

    # Fields that never need an audit line of their own.
    _mos_quiet_fields = {'write_date', 'write_uid', '__last_update', 'message_ids', 'message_follower_ids'}

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self.env['mos.audit.event'].record('create', rec, rec.display_name)
        return records

    def write(self, vals):
        changed = sorted(set(vals) - self._mos_quiet_fields)
        result = super().write(vals)
        if changed:
            for rec in self:
                self.env['mos.audit.event'].record('write', rec, f'{rec.display_name}: {", ".join(changed)}')
        return result

    def unlink(self):
        for rec in self:
            self.env['mos.audit.event'].record('delete', rec, rec.display_name)
        return super().unlink()


class MosPosted(models.AbstractModel):
    """Posted documents cannot be edited or deleted (FR-PLT-021); corrections are new documents (FR-PLT-022).

    A model that inherits this declares which states are final in ``_mos_final_states`` and, if some
    fields stay editable after posting, lists them in ``_mos_editable_after_post``.
    """

    _name = 'mos.posted'
    _inherit = 'mos.audited'
    _description = 'MOS postable document'

    _mos_final_states = ('posted',)
    _mos_editable_after_post = set()

    def write(self, vals):
        for rec in self:
            if rec.state in self._mos_final_states and set(vals) - self._mos_editable_after_post - self._mos_quiet_fields:
                raise UserError(self.env._('%s is posted and cannot be changed. Create a reversal or a superseding document instead.', rec.display_name))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state in self._mos_final_states:
                raise UserError(self.env._('%s is posted and cannot be deleted.', rec.display_name))
        return super().unlink()
