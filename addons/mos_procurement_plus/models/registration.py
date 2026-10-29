import secrets
from datetime import timedelta

from markupsafe import Markup

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
    """Staff register a supplier in five steps, as on the client's supplier portal screens (pages 3 and 4): basic information, contacts,
    address, financial information, products and services (FR-PRC-001). Next checks the step it leaves; Register checks them all."""

    _name = 'mos.supplier.registration'
    _description = 'MOS supplier registration'

    STEPS = [('basic', 'Basic Info'), ('contacts', 'Contacts'), ('address', 'Address'), ('financial', 'Financial'), ('products', 'Products & Services')]

    step = fields.Selection(STEPS, default='basic', required=True)
    rail = fields.Html(compute='_compute_rail', sanitize=False)
    # 1 basic information
    name = fields.Char(string='Supplier name')
    registration_no = fields.Char(string='Business registration number')
    vat = fields.Char(string='Tax identification number (TIN)')
    description = fields.Text(string='Company description')
    trade_licence = fields.Char(string='Trade licence number')
    licence_expires = fields.Date(string='Licence expires')
    self_billing = fields.Boolean(string='Farmer or trader without invoices')
    # 2 contacts
    phone = fields.Char(string='Company phone')
    email = fields.Char(string='Company email')
    contact_ids = fields.One2many('mos.supplier.registration.contact', 'registration_id', string='Contact people')
    # 3 address
    country_id = fields.Many2one('res.country', string='Country')
    state_id = fields.Many2one('res.country.state', string='Region / State', domain="[('country_id', '=', country_id)]")
    city = fields.Char()
    zip = fields.Char(string='Postal code')
    address = fields.Text(string='Physical address')
    same_as_physical = fields.Boolean(string='Mailing address is the same as the physical address')
    mailing_address = fields.Text()
    # 4 financial information
    bank_ids = fields.One2many('mos.supplier.registration.bank', 'registration_id', string='Bank accounts')
    # 5 products and services
    activity = fields.Text(string='What they supply')

    @api.depends('step')
    def _compute_rail(self):
        keys = [key for key, _label in self.STEPS]
        for wizard in self:
            current = keys.index(wizard.step or 'basic')
            items = []
            for number, (key, label) in enumerate(self.STEPS, start=1):
                state = 'current' if number - 1 == current else 'done' if number - 1 < current else 'todo'
                items.append(Markup('<li class="mos-step mos-step-{}"><span class="mos-step-dot">{}</span><span>{}</span></li>').format(state, '✓' if state == 'done' else number, self.env._(label)))
            wizard.rail = Markup('<ol class="mos-steps">{}</ol>').format(Markup('').join(items))

    @api.onchange('same_as_physical', 'address')
    def _onchange_same_as_physical(self):
        if self.same_as_physical:
            self.mailing_address = self.address

    # -- checks, one set for each step ------------------------------------------------------------------------------------------
    def _missing(self, step):
        """The labels of what the step still needs."""
        self.ensure_one()
        needs = {
            'basic': ('name', 'vat', 'trade_licence', 'licence_expires'),
            'contacts': (),
            'address': ('country_id', 'address'),
            'financial': (),
            'products': (),
        }[step]
        missing = [self._fields[f].string for f in needs if not self[f]]
        if step == 'contacts' and not (self.phone or self.email or self.contact_ids):
            missing.append(self.env._('a phone, an email or a contact person'))
        if step == 'financial':
            for bank in self.bank_ids:
                if not (bank.bank_name and bank.account_number):
                    missing.append(self.env._('the bank name and the account number of every account'))
                    break
        return missing

    def _check_step(self, step):
        missing = self._missing(step)
        if missing:
            raise UserError(self.env._('Before going on, fill in: %s.', ', '.join(missing)))

    def _reopen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Register a supplier'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'dialog_size': 'extra-large'},
        }

    def action_next(self):
        self.ensure_one()
        keys = [key for key, _label in self.STEPS]
        self._check_step(self.step)
        self.step = keys[min(keys.index(self.step) + 1, len(keys) - 1)]
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        keys = [key for key, _label in self.STEPS]
        self.step = keys[max(keys.index(self.step) - 1, 0)]
        return self._reopen()

    # -- the supplier -------------------------------------------------------------------------------------------------------------
    def _partner_values(self):
        self.ensure_one()
        notes = [text for text in (self.description, self.activity and self.env._('Supplies: %s', self.activity)) if text]
        return {
            'name': self.name,
            'vat': self.vat.strip(),
            'mos_registration_no': self.registration_no,
            'phone': self.phone,
            'email': self.email,
            'street': self.address,
            'street2': False if self.same_as_physical or not self.mailing_address else self.env._('Mailing: %s', self.mailing_address),
            'city': self.city,
            'zip': self.zip,
            'state_id': self.state_id.id,
            'country_id': self.country_id.id,
            'comment': '\n'.join(notes) or False,
            'is_company': True,
            'supplier_rank': 1,
            'mos_self_billing': self.self_billing,
        }

    def action_register(self):
        self.ensure_one()
        for step, _label in self.STEPS:
            self._check_step(step)
        if self.env['res.partner'].sudo().search_count([('vat', '=', self.vat.strip())]):
            raise UserError(self.env._('A supplier with this TIN is already registered.'))
        partner = self.env['res.partner'].create(self._partner_values())
        for person in self.contact_ids:
            self.env['res.partner'].create({'parent_id': partner.id, 'type': 'contact', 'name': person.name, 'function': person.role, 'phone': person.phone, 'email': person.email})
        self.env['mos.supplier.document'].create({'partner_id': partner.id, 'doc_type': 'trade_licence', 'number': self.trade_licence, 'expires_on': self.licence_expires})
        self.env['mos.supplier.document'].create({'partner_id': partner.id, 'doc_type': 'tin', 'number': self.vat.strip()})
        for bank in self.bank_ids:
            reason = self.env._('Registered with the supplier (%(bank)s, account name %(holder)s, %(currency)s)', bank=bank.bank_name, holder=bank.account_name or '-', currency=bank.currency_id.name or '-')
            self.env['mos.supplier.bank.change'].create({'partner_id': partner.id, 'new_account': bank.account_number, 'reason': reason})
        return {'type': 'ir.actions.act_window', 'res_model': 'res.partner', 'res_id': partner.id, 'view_mode': 'form', 'target': 'current'}


class MosSupplierRegistrationContact(models.TransientModel):
    _name = 'mos.supplier.registration.contact'
    _description = 'MOS supplier registration: contact person'

    registration_id = fields.Many2one('mos.supplier.registration', required=True, ondelete='cascade')
    name = fields.Char(required=True)
    role = fields.Char(string='Position')
    phone = fields.Char()
    email = fields.Char()


class MosSupplierRegistrationBank(models.TransientModel):
    _name = 'mos.supplier.registration.bank'
    _description = 'MOS supplier registration: bank account'

    registration_id = fields.Many2one('mos.supplier.registration', required=True, ondelete='cascade')
    bank_name = fields.Char(string='Bank name')
    account_name = fields.Char()
    account_number = fields.Char(string='Account number')
    currency_id = fields.Many2one('res.currency', string='Currency')
