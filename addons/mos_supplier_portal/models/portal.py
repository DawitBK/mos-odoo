import base64

from odoo import api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_portal_user_ids = fields.One2many('res.users', 'partner_id', string='Portal users', domain=[('share', '=', True)])

    def mos_portal_invite(self):
        """Give a supplier's contact a portal sign-in and return the page where they set their own password."""
        self.ensure_one()
        user = self.env.user
        if not (user.has_group('mos_procurement.group_mos_procurement_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a procurement manager can invite a supplier to the portal.'))
        partner = self.sudo()
        if partner.supplier_rank <= 0 or partner.mos_supplier_state not in ('approved', 'candidate', 'submitted'):
            raise UserError(self.env._('Only a supplier that is registered and not blocked can be invited.'))
        if not (partner.email or '').strip():
            raise UserError(self.env._('Record the supplier\'s e-mail address first: it is their sign-in name.'))
        portal = self.env.ref('base.group_portal')
        login = partner.email.strip().lower()
        existing = self.env['res.users'].sudo().with_context(active_test=False).search([('login', '=', login)], limit=1)
        if existing and existing.partner_id != partner:
            raise UserError(self.env._('The address %s is already someone else\'s sign-in.', login))
        if not existing:
            existing = self.env['res.users'].sudo().with_context(no_reset_password=True).create({'name': partner.name, 'login': login, 'email': login, 'partner_id': partner.id, 'group_ids': [(6, 0, [portal.id])]})
        elif not existing.active:
            existing.active = True
        partner.signup_prepare()
        url = partner._get_signup_url()
        self.env['mos.audit.event'].record('supplier_portal_invited', self, f'{partner.name}: {login}')
        return url

    def action_mos_portal_invite(self):
        url = self.mos_portal_invite()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': self.env._('Portal sign-in ready'), 'message': self.env._('Send this link to the supplier so they can choose a password: %s', url), 'sticky': True, 'type': 'success'},
        }


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    mos_promise_date = fields.Date(string='Supplier promises', help='The delivery date the supplier committed to on the portal.')
    mos_promise_note = fields.Char(string='Supplier note')


class MosSupplierSubmission(models.Model):
    """An invoice a supplier sent through the portal: its number, its amount and the file. The payables clerk reviews it and enters the
    invoice into the books; nothing here is booked automatically."""

    _name = 'mos.supplier.submission'
    _inherit = ['mail.thread']
    _description = 'MOS supplier invoice submission'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    supplier_id = fields.Many2one('res.partner', required=True, readonly=True)
    order_id = fields.Many2one('purchase.order', readonly=True)
    supplier_ref = fields.Char(string='Supplier invoice number', required=True, readonly=True)
    amount = fields.Float(readonly=True)
    attachment_ids = fields.One2many('mos.attachment', compute='_compute_attachments', string='Files')
    state = fields.Selection([('new', 'New'), ('reviewed', 'Entered by payables'), ('rejected', 'Rejected')], default='new', required=True, readonly=True, tracking=True)
    review_note = fields.Char(help='What the supplier is told when an invoice is rejected.')
    reviewed_by_id = fields.Many2one('res.users', readonly=True)
    invoice_id = fields.Many2one('mos.supplier.invoice', readonly=True)

    _ref_once = models.Constraint('unique(supplier_id, supplier_ref)', 'You have already sent an invoice with this number.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.supplier.submission')
        return super().create(vals_list)

    def attach(self, filename, content):
        """Keep the supplier's file through the MOS attachment rules (type by content, size, SHA-256, scan)."""
        self.ensure_one()
        return self.env['mos.attachment'].sudo().create({'name': filename, 'res_model': self._name, 'res_id': self.id, 'file': base64.b64encode(content).decode()})

    def action_review(self, invoice=None):
        for sub in self:
            sub._decide('reviewed', sub.review_note or '', invoice)

    def action_reject(self, note=None):
        for sub in self:
            reason = note or sub.review_note
            if not reason:
                raise UserError(self.env._('Say why the invoice is rejected: the supplier will see it.'))
            sub._decide('rejected', reason)

    def _decide(self, state, note, invoice=None):
        user = self.env.user
        if not (user.has_group('mos_payables.group_mos_ap_clerk') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only the payables clerk can review a submitted invoice.'))
        for sub in self:
            if sub.state != 'new':
                raise UserError(self.env._('This submission has been reviewed already.'))
            sub.sudo().write({'state': state, 'review_note': note, 'reviewed_by_id': user.id, 'invoice_id': invoice.id if invoice else False})
            self.env['mos.audit.event'].record('supplier_submission_' + state, sub, f'{sub.name}: {sub.supplier_ref} {note}')

    def _compute_attachments(self):
        for sub in self:
            sub.attachment_ids = self.env['mos.attachment'].sudo().search([('res_model', '=', self._name), ('res_id', '=', sub.id)])
