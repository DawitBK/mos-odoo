import os
import re
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

DOC_TYPES = [
    ('trade_licence', 'Trade licence'),
    ('tin', 'TIN certificate'),
    ('vat', 'VAT registration'),
    ('bank_letter', 'Bank letter'),
    ('other', 'Other'),
]


def _cipher(env):
    """The key comes from MOS_BANK_KEY when set, otherwise from a generated system parameter."""
    key = os.environ.get('MOS_BANK_KEY') or env['ir.config_parameter'].sudo().get_str('mos_procurement.bank_key')
    if not key:
        key = Fernet.generate_key().decode()
        env['ir.config_parameter'].sudo().set_str('mos_procurement.bank_key', key)
    return Fernet(key.encode())


def _mask(account):
    digits = re.sub(r'\s', '', account or '')
    return ('*' * max(len(digits) - 4, 0)) + digits[-4:] if digits else ''


class ResPartner(models.Model):
    """Supplier standing: candidate, approved, suspended or blocked, with the documents that justify it (FR-PRC-001..008)."""

    _inherit = 'res.partner'

    mos_supplier_state = fields.Selection(
        [('candidate', 'Candidate'), ('approved', 'Approved'), ('suspended', 'Suspended'), ('blocked', 'Blocked')],
        string='Supplier standing',
        default='candidate',
        copy=False,
        tracking=True,
    )
    mos_status_reason = fields.Char(string='Standing reason', copy=False)
    mos_approved_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    mos_approved_on = fields.Date(readonly=True, copy=False)
    mos_review_due = fields.Date(string='Next review', copy=False)
    mos_document_ids = fields.One2many('mos.supplier.document', 'partner_id', string='Supplier documents')
    mos_documents_ok = fields.Boolean(compute='_compute_documents_ok', string='Documents complete and valid')
    mos_missing_documents = fields.Char(compute='_compute_documents_ok')
    mos_bank_account_enc = fields.Text(copy=False, groups='mos_procurement.group_mos_procurement_manager,mos_core.group_mos_admin')
    mos_bank_account = fields.Char(compute='_compute_bank_masked', string='Payment account', store=False, help='Masked: only the last four digits show.')
    mos_bank_change_ids = fields.One2many('mos.supplier.bank.change', 'partner_id', string='Bank changes')

    # -- documents ----------------------------------------------------------------------------------------
    @api.model
    def _mos_required_doc_types(self):
        raw = self.env['ir.config_parameter'].sudo().get_str('mos_procurement.required_documents', 'trade_licence,tin')
        return [t.strip() for t in raw.split(',') if t.strip()]

    @api.depends('mos_document_ids.expires_on', 'mos_document_ids.doc_type')
    def _compute_documents_ok(self):
        today = fields.Date.context_today(self)
        required = self._mos_required_doc_types()
        for partner in self:
            valid = partner.mos_document_ids.filtered(lambda d: not d.expires_on or d.expires_on >= today).mapped('doc_type')
            missing = [label for key, label in DOC_TYPES if key in required and key not in valid]
            partner.mos_documents_ok = not missing
            partner.mos_missing_documents = ', '.join(missing)

    # -- standing -----------------------------------------------------------------------------------------
    def _mos_require_procurement_manager(self):
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can change a supplier standing.'))

    def action_approve_supplier(self):
        self._mos_require_procurement_manager()
        months = self.env['ir.config_parameter'].sudo().get_int('mos_procurement.review_months', 12)
        for partner in self:
            if partner.mos_supplier_state == 'blocked':
                raise UserError(self.env._('A blocked supplier must be reinstated by a general manager first.'))
            if not partner.mos_documents_ok:
                raise UserError(self.env._('%(name)s cannot be approved: missing or expired %(docs)s.', name=partner.display_name, docs=partner.mos_missing_documents))
            if partner.create_uid == self.env.user and not self.env.user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The person who registered a supplier cannot approve it.'))
            partner.sudo().write(
                {
                    'mos_supplier_state': 'approved',
                    'mos_status_reason': False,
                    'mos_approved_by_id': self.env.uid,
                    'mos_approved_on': fields.Date.context_today(self),
                    'mos_review_due': fields.Date.context_today(self) + timedelta(days=30 * months),
                    'supplier_rank': max(partner.supplier_rank, 1),
                }
            )
            self.env['mos.audit.event'].record('supplier_approved', partner, partner.display_name)

    def action_suspend_supplier(self, reason=''):
        self._mos_require_procurement_manager()
        if not reason:
            raise UserError(self.env._('Give the reason for suspending the supplier.'))
        for partner in self:
            partner.sudo().write({'mos_supplier_state': 'suspended', 'mos_status_reason': reason})
            self.env['mos.audit.event'].record('supplier_suspended', partner, f'{partner.display_name}: {reason}')

    def action_block_supplier(self, reason=''):
        if not (self.env.user.has_group('mos_approvals.group_mos_general_manager') or self.env.user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a general manager can block a supplier.'))
        if not reason:
            raise UserError(self.env._('Give the reason for blocking the supplier.'))
        for partner in self:
            partner.sudo().write({'mos_supplier_state': 'blocked', 'mos_status_reason': reason})
            self.env['mos.audit.event'].record('supplier_blocked', partner, f'{partner.display_name}: {reason}')

    def action_reinstate_supplier(self):
        if not (self.env.user.has_group('mos_approvals.group_mos_general_manager') or self.env.user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a general manager can reinstate a blocked or suspended supplier.'))
        for partner in self:
            partner.sudo().write({'mos_supplier_state': 'candidate', 'mos_status_reason': False})
            self.env['mos.audit.event'].record('supplier_reinstated', partner, partner.display_name)

    # -- bank account ---------------------------------------------------------------------------------------
    def _compute_bank_masked(self):
        for partner in self:
            partner.mos_bank_account = _mask(partner._mos_bank_plain(audit=False)) if partner.sudo().mos_bank_account_enc else False

    def _mos_bank_plain(self, audit=True):
        """The full account number, for the payment run only; every reveal is audited."""
        self.ensure_one()
        token = self.sudo().mos_bank_account_enc
        if not token:
            return ''
        try:
            plain = _cipher(self.env).decrypt(token.encode()).decode()
        except InvalidToken:
            raise UserError(self.env._('The stored account of %s cannot be read with the current key.', self.display_name))
        if audit:
            self.env['mos.audit.event'].record('bank_account_revealed', self, self.display_name)
        return plain

    def reveal_bank_account(self):
        self.ensure_one()
        user = self.env.user
        if not (user.has_group('mos_approvals.group_mos_finance_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a finance manager can see a full account number.'))
        return self._mos_bank_plain()

    def write(self, vals):
        if 'mos_bank_account_enc' in vals and not self.env.context.get('mos_bank_apply'):
            raise UserError(self.env._('A supplier account is changed through a bank change request, never directly.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('mos_bank_account_enc') and not self.env.context.get('mos_bank_apply'):
                raise UserError(self.env._('A supplier account is set through a bank change request, never directly.'))
        return super().create(vals_list)


class MosSupplierDocument(models.Model):
    """A document that supports a supplier's approval, with its expiry (FR-PRC-004, 005)."""

    _name = 'mos.supplier.document'
    _inherit = ['mos.audited']
    _description = 'MOS supplier document'
    _order = 'partner_id, doc_type, expires_on desc'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    doc_type = fields.Selection(DOC_TYPES, required=True, default='trade_licence')
    number = fields.Char(required=True)
    issued_on = fields.Date()
    expires_on = fields.Date()
    file = fields.Binary(attachment=True)
    filename = fields.Char()
    notified_on = fields.Date(readonly=True, copy=False)
    state = fields.Selection([('valid', 'Valid'), ('expiring', 'Expiring soon'), ('expired', 'Expired')], compute='_compute_state')

    @api.constrains('issued_on', 'expires_on')
    def _check_dates(self):
        for doc in self:
            if doc.issued_on and doc.expires_on and doc.expires_on < doc.issued_on:
                raise ValidationError(self.env._('A document cannot expire before it was issued.'))

    @api.depends('expires_on')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        soon = today + timedelta(days=self.env['ir.config_parameter'].sudo().get_int('mos_procurement.expiry_notice_days', 30))
        for doc in self:
            if doc.expires_on and doc.expires_on < today:
                doc.state = 'expired'
            elif doc.expires_on and doc.expires_on <= soon:
                doc.state = 'expiring'
            else:
                doc.state = 'valid'

    @api.model
    def _cron_documents(self):
        """Daily: warn the buyers about documents about to expire; suspend approved suppliers whose required document lapsed."""
        today = fields.Date.context_today(self)
        days = self.env['ir.config_parameter'].sudo().get_int('mos_procurement.expiry_notice_days', 30)
        managers = self.env.ref('mos_procurement.group_mos_procurement_manager').sudo().user_ids
        required = self.env['res.partner']._mos_required_doc_types()
        expiring = self.search([('expires_on', '>=', today), ('expires_on', '<=', today + timedelta(days=days)), ('notified_on', '=', False)])
        for doc in expiring:
            for manager in managers:
                doc.partner_id.sudo().activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=doc.expires_on,
                    summary=self.env._('%(type)s of %(supplier)s expires on %(date)s', type=dict(DOC_TYPES)[doc.doc_type], supplier=doc.partner_id.display_name, date=doc.expires_on),
                    user_id=manager.id,
                )
            doc.sudo().write({'notified_on': today})
        lapsed = self.search([('expires_on', '<', today), ('doc_type', 'in', required), ('partner_id.mos_supplier_state', '=', 'approved')])
        for partner in lapsed.partner_id:
            if not partner.mos_documents_ok:
                partner.sudo().write({'mos_supplier_state': 'suspended', 'mos_status_reason': self.env._('Required document expired: %s', partner.mos_missing_documents)})
                self.env['mos.audit.event'].record('supplier_suspended', partner, f'{partner.display_name}: required document expired')
        return len(expiring), len(lapsed.partner_id)


class MosSupplierBankChange(models.Model):
    """Changing where a supplier is paid is the classic fraud: asked for, verified by a call-back and applied by three different people (FR-PRC-009..012)."""

    _name = 'mos.supplier.bank.change'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS supplier bank change'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected')

    name = fields.Char(readonly=True, copy=False, default='New')
    partner_id = fields.Many2one('res.partner', required=True, ondelete='restrict', domain="[('supplier_rank', '>', 0)]")
    new_account_enc = fields.Text(readonly=True)
    new_account_masked = fields.Char(readonly=True)
    reason = fields.Char(required=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    verified_by_id = fields.Many2one('res.users', readonly=True)
    verification_note = fields.Char(readonly=True, help='Who was called back on a number already on file, and what they confirmed.')
    applied_by_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection(
        [('requested', 'Requested'), ('verified', 'Verified by call-back'), ('applied', 'Applied'), ('rejected', 'Rejected')], default='requested', required=True, readonly=True, tracking=True
    )
    # Typed once on the form; stored encrypted and cleared at once.
    new_account = fields.Char(string='New account number', store=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            account = vals.pop('new_account', None)
            if not account:
                raise UserError(self.env._('Enter the new account number.'))
            if not re.fullmatch(r'[0-9A-Za-z\- ]{6,34}', account.strip()):
                raise ValidationError(self.env._('That does not look like an account number.'))
            vals['new_account_enc'] = _cipher(self.env).encrypt(account.strip().encode()).decode()
            vals['new_account_masked'] = _mask(account.strip())
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.supplier.bank.change')
        return super().create(vals_list)

    def action_verify(self, note):
        for change in self:
            if change.state != 'requested':
                raise UserError(self.env._('Only a requested change can be verified.'))
            if not note:
                raise UserError(self.env._('Record who was called back and what they confirmed.'))
            if change.requested_by_id == self.env.user:
                raise UserError(self.env._('The person who asked for the change cannot verify it.'))
            change.write({'state': 'verified', 'verified_by_id': self.env.uid, 'verification_note': note})

    def action_apply(self):
        user = self.env.user
        if not (user.has_group('mos_approvals.group_mos_finance_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a finance manager can apply a bank change.'))
        for change in self:
            if change.state != 'verified':
                raise UserError(self.env._('Verify the change by call-back before it is applied.'))
            if user in (change.requested_by_id, change.verified_by_id):
                raise UserError(self.env._('The change must be applied by someone other than the requester and the verifier.'))
            change.partner_id.sudo().with_context(mos_bank_apply=True).write({'mos_bank_account_enc': change.sudo().new_account_enc})
            change.write({'state': 'applied', 'applied_by_id': self.env.uid})
            self.env['mos.audit.event'].record('bank_account_changed', change.partner_id, f'{change.partner_id.display_name} -> {change.new_account_masked}')

    def action_reject(self, reason=''):
        for change in self:
            if change.state in ('applied', 'rejected'):
                raise UserError(self.env._('This change is already closed.'))
            change.write({'state': 'rejected', 'verification_note': reason or change.verification_note})
