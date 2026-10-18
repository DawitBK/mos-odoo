from odoo import api, fields, models


class MosExceptionDigest(models.AbstractModel):
    """A daily e-mail to each person who has exceptions assigned to them, listing what is open and what is overdue (FR-PLT-053).

    It is off until `mos_platform_plus.digest_enabled` is set. The mails are queued; they leave the server only when
    an outgoing mail server is configured, so nothing is lost while there is none.
    """

    _name = 'mos.exception.digest'
    _description = 'MOS exception digest'

    @api.model
    def _enabled(self):
        return self.env['ir.config_parameter'].sudo().get_bool('mos_platform_plus.digest_enabled', False)

    @api.model
    def _cron_send(self):
        if not self._enabled():
            return 0
        return len(self._send())

    @api.model
    def _send(self):
        exceptions = self.env['mos.exception'].sudo().search([('state', 'in', ('open', 'assigned')), ('assignee_id', '!=', False)])
        mails = self.env['mail.mail'].sudo()
        for user in exceptions.mapped('assignee_id'):
            email = user.email or user.partner_id.email
            if not email:
                continue
            mine = exceptions.filtered(lambda e, u=user: e.assignee_id == u)
            overdue = mine.filtered('overdue')
            rows = ''.join(f"<li>{'<b>OVERDUE</b> ' if e in overdue else ''}{e.name}: {e.summary} ({e.severity})</li>" for e in mine.sorted(lambda e: (e.overdue is False, e.due_date or fields.Date.today())))
            mails |= self.env['mail.mail'].sudo().create(
                {
                    'subject': self.env._('MOS: %(n)s open exceptions, %(o)s overdue', n=len(mine), o=len(overdue)),
                    'email_to': email,
                    'body_html': f'<p>{self.env._("These items need your attention:")}</p><ul>{rows}</ul>',
                    'auto_delete': True,
                }
            )
        return mails
