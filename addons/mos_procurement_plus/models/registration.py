import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class MosSupplierInvite(models.Model):
    """A single-use, expiring link a buyer sends to a prospective supplier. The supplier fills in the company form; a candidate
    supplier is created for procurement to check and approve (FR-PRC-001, 020)."""

    _name = 'mos.supplier.invite'
    _inherit = ['mos.audited']
    _description = 'MOS supplier invitation'
    _order = 'id desc'

    name = fields.Char(string='Who is invited', required=True, help='The company or person the link is for.')
    email = fields.Char()
    token = fields.Char(readonly=True, copy=False, index=True)
    expires_at = fields.Datetime(readonly=True)
    invited_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    partner_id = fields.Many2one('res.partner', readonly=True, string='Registered as')
    state = fields.Selection([('open', 'Link sent'), ('registered', 'Registered'), ('expired', 'Expired')], default='open', required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        days = self.env['ir.config_parameter'].sudo().get_int('mos_procurement_plus.invite_days', 7)
        for vals in vals_list:
            vals['token'] = secrets.token_urlsafe(24)
            vals['expires_at'] = fields.Datetime.now() + timedelta(days=days)
        return super().create(vals_list)

    def path(self):
        self.ensure_one()
        return f'/mos/supplier/register/{self.token}'

    @api.model
    def for_token(self, token):
        invite = self.sudo().search([('token', '=', token), ('state', '=', 'open')], limit=1) if token else self.browse()
        if invite and invite.expires_at < fields.Datetime.now():
            invite.state = 'expired'
            return self.browse()
        return invite

    def register(self, vals):
        """Create the candidate supplier from the form the supplier filled in. The link then stops working."""
        self.ensure_one()
        if self.state != 'open':
            raise UserError(self.env._('This invitation has already been used or has expired.'))
        name = (vals.get('name') or '').strip()
        if not name or not (vals.get('vat') or '').strip():
            raise UserError(self.env._('The company name and the TIN are required.'))
        existing = self.env['res.partner'].sudo().search([('vat', '=', vals['vat'].strip())], limit=1)
        if existing:
            raise UserError(self.env._('A supplier with this TIN is already registered.'))
        partner = self.env['res.partner'].sudo().create(
            {
                'name': name[:120],
                'vat': vals['vat'].strip()[:30],
                'email': (vals.get('email') or self.email or '')[:120],
                'phone': (vals.get('phone') or '')[:40],
                'street': (vals.get('address') or '')[:200],
                'comment': (vals.get('activity') or '')[:500],
                'is_company': True,
                'supplier_rank': 1,
                'mos_supplier_state': 'candidate',
            }
        )
        self.sudo().write({'partner_id': partner.id, 'state': 'registered', 'token': False})
        self.env['mos.audit.event'].sudo().record('supplier_self_registered', partner, f'{partner.display_name} via invitation {self.id}')
        return partner

    @api.model
    def _cron_expire(self):
        late = self.sudo().search([('state', '=', 'open'), ('expires_at', '<', fields.Datetime.now())])
        late.write({'state': 'expired', 'token': False})
        return len(late)


class MosSupplierRegistration(models.TransientModel):
    """Staff register a supplier in five short steps on one page: company, tax, contact, bank, activity (FR-PRC-001)."""

    _name = 'mos.supplier.registration'
    _description = 'MOS supplier registration'

    name = fields.Char(string='Company or trading name', required=True)
    vat = fields.Char(string='TIN', required=True)
    trade_licence = fields.Char(string='Trade licence number', required=True)
    licence_expires = fields.Date(required=True)
    contact = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    address = fields.Char()
    bank_name = fields.Char()
    bank_account = fields.Char(string='Account number (goes through a bank change request)')
    activity = fields.Text(string='What they supply')
    self_billing = fields.Boolean(string='Farmer or trader without invoices')

    def action_register(self):
        self.ensure_one()
        if self.env['res.partner'].sudo().search_count([('vat', '=', self.vat.strip())]):
            raise UserError(self.env._('A supplier with this TIN is already registered.'))
        partner = self.env['res.partner'].create(
            {
                'name': self.name,
                'vat': self.vat.strip(),
                'phone': self.phone,
                'email': self.email,
                'street': self.address,
                'comment': self.activity,
                'is_company': True,
                'supplier_rank': 1,
                'mos_self_billing': self.self_billing,
            }
        )
        self.env['mos.supplier.document'].create({'partner_id': partner.id, 'doc_type': 'trade_licence', 'number': self.trade_licence, 'expires_on': self.licence_expires})
        self.env['mos.supplier.document'].create({'partner_id': partner.id, 'doc_type': 'tin', 'number': self.vat.strip()})
        if self.bank_account:
            self.env['mos.supplier.bank.change'].create({'partner_id': partner.id, 'new_account': self.bank_account, 'reason': self.env._('Registered with the supplier (%s)', self.bank_name or '')})
        return {'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'res_id': partner.id, 'view_mode': 'form', 'target': 'current'}
