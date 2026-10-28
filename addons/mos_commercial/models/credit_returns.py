import secrets
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


def _has(user, *groups):
    return any(user.has_group(g) for g in groups)


class MosCreditNote(models.Model):
    """What the customer is credited, for a shortage, a damage claim, a price correction or goods that came back (FR-SAL-018).

    A credit without a physical return goes through the approval matrix; once posted it is numbered without gaps, never
    edited, and lowers what the invoice still owes."""

    _name = 'mos.credit.note'
    _inherit = ['mos.posted', 'mos.approvable', 'mail.thread']
    _description = 'MOS customer credit note'
    _order = 'id desc'
    _mos_final_states = ('posted', 'cancelled')
    _mos_editable_after_post = {'state', 'mos_approval_request_id', 'mos_approval_state', 'posted_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    customer_id = fields.Many2one('res.partner', required=True)
    invoice_id = fields.Many2one('mos.customer.invoice', domain="[('customer_id', '=', customer_id)]")
    reason_id = fields.Many2one('mos.reason', required=True, domain=[('domain', '=', 'credit')])
    amount = fields.Float(required=True)
    date = fields.Date(default=fields.Date.context_today)
    note = fields.Char()
    return_id = fields.Many2one('mos.customer.return', readonly=True)
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    posted_by_id = fields.Many2one('res.users', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('posted', 'Posted'), ('cancelled', 'Cancelled')],
        default='draft',
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('amount', 0) <= 0:
                raise ValidationError(self.env._('A credit note must be above zero.'))
            invoice = self.env['mos.customer.invoice'].browse(vals.get('invoice_id'))
            if invoice and invoice.customer_id.id != vals['customer_id']:
                raise ValidationError(self.env._('That invoice belongs to another customer.'))
        return super().create(vals_list)

    def _mos_approval_amount(self):
        self.ensure_one()
        return self.amount

    def _mos_approval_done(self, request):
        self.sudo().write({'mos_approval_state': 'approved', 'state': 'approved'})

    def _mos_approval_closed(self, request, state):
        if state != 'approved':
            self.sudo().write({'mos_approval_state': state, 'state': 'draft' if state in ('returned', 'cancelled') else 'cancelled'})

    def action_submit(self):
        dialog = self._mos_attestation_dialog('action_submit')
        if dialog:
            return dialog
        for note in self:
            if note.state != 'draft':
                raise UserError(self.env._('Only a draft credit note can be submitted.'))
            if note.invoice_id and note.amount > note.invoice_id.balance + 0.005:
                raise UserError(self.env._('The credit (%(credit)s) is more than the %(owed)s still owed on the invoice.', credit=note.amount, owed=note.invoice_id.balance))
            if note._mos_approval_required():
                note.action_mos_submit_approval()
                note.write({'state': 'pending'})
            else:
                note.write({'state': 'approved', 'mos_approval_state': 'approved'})

    def action_post(self):
        for note in self:
            if note.state != 'approved':
                raise UserError(self.env._('Only an approved credit note can be posted.'))
            if note.requested_by_id == self.env.user and not note.mos_approval_request_id and not self.env.user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('A credit note that needed no approval must be posted by someone other than its author.'))
            note.write({'name': self.env['ir.sequence'].next_by_code('mos.credit.note'), 'state': 'posted', 'posted_by_id': self.env.uid})
            if note.invoice_id:
                note.invoice_id.invalidate_recordset(['paid_amount', 'balance'])
            self.env['mos.audit.event'].record('credit_note_posted', note, f'{note.name}: {note.amount} to {note.customer_id.display_name}')

    def action_cancel(self):
        for note in self:
            if note.state in ('posted', 'cancelled'):
                raise UserError(self.env._('This credit note is final.'))
            if note.state == 'pending':
                note.action_mos_recall_approval()
            note.write({'state': 'cancelled'})


class MosCustomerInvoice(models.Model):
    _inherit = 'mos.customer.invoice'

    credit_note_ids = fields.One2many('mos.credit.note', 'invoice_id')

    @api.depends('receipt_ids.amount', 'gross_amount', 'credit_note_ids.amount', 'credit_note_ids.state')
    def _compute_paid(self):
        for inv in self:
            credited = sum(inv.credit_note_ids.filtered(lambda c: c.state == 'posted').mapped('amount'))
            inv.paid_amount = sum(inv.receipt_ids.mapped('amount'))
            inv.balance = round(inv.gross_amount - inv.paid_amount - credited, 2)


class MosReason(models.Model):
    _inherit = 'mos.reason'

    domain = fields.Selection(selection_add=[('credit', 'Customer credit'), ('customer_return', 'Customer return')], ondelete={'credit': 'cascade', 'customer_return': 'cascade'})


class MosCustomerReturn(models.Model):
    """Goods that came back from a customer are received into quarantine, decided on by quality (restock or scrap) and, where
    the customer is owed, credited (FR-SAL-009)."""

    _name = 'mos.customer.return'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS customer return'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    site_id = fields.Many2one('mos.site', required=True, default=lambda self: self.env.user.mos_site_ids[:1])
    customer_id = fields.Many2one('res.partner', required=True)
    delivery_id = fields.Many2one('mos.delivery.order', domain="[('partner_id', '=', customer_id), ('state', '=', 'dispatched')]")
    product_id = fields.Many2one('product.product', required=True)
    qty = fields.Float(required=True, digits=(14, 1))
    reason_id = fields.Many2one('mos.reason', required=True, domain=[('domain', '=', 'customer_return')])
    note = fields.Char()
    received_by_id = fields.Many2one('res.users', readonly=True)
    decided_by_id = fields.Many2one('res.users', readonly=True)
    decision = fields.Selection([('restock', 'Restock'), ('scrap', 'Scrap')], readonly=True)
    credit_note_id = fields.Many2one('mos.credit.note', readonly=True)
    state = fields.Selection([('draft', 'Announced'), ('received', 'In quarantine'), ('decided', 'Decided'), ('cancelled', 'Cancelled')], default='draft', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('qty', 0) <= 0:
                raise ValidationError(self.env._('Return a quantity above zero.'))
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.customer.return')
        return super().create(vals_list)

    def action_receive(self):
        """The goods are counted in at the gate and booked into quarantine (never straight to available stock)."""
        for ret in self:
            if ret.state != 'draft':
                raise UserError(self.env._('Only an announced return can be received.'))
            if ret.delivery_id:
                sold = ret.delivery_id.sudo().loaded_qty
                done = sum(self.search([('delivery_id', '=', ret.delivery_id.id), ('state', 'in', ('received', 'decided')), ('id', '!=', ret.id)]).mapped('qty'))
                if ret.qty + done > sold + 0.0001:
                    raise UserError(self.env._('Only %(sold)s kg left on that delivery, %(back)s kg has already come back.', sold=sold, back=done))
            quarantine = self.env['mos.stock.status.change'].status_location(ret.site_id, 'quarantine')
            ret._move(ret.product_id, ret.qty, self.env.ref('stock.stock_location_customers'), quarantine)
            ret.write({'state': 'received', 'received_by_id': self.env.uid})
            self.env['mos.audit.event'].record('customer_return_received', ret, f'{ret.name}: {ret.qty} {ret.product_id.display_name}')

    def action_decide(self, decision, note=''):
        user = self.env.user
        if not _has(user, 'mos_lab.group_mos_lab_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a lab manager can decide on returned goods.'))
        if decision not in ('restock', 'scrap'):
            raise UserError(self.env._('Choose restock or scrap.'))
        for ret in self:
            if ret.state != 'received':
                raise UserError(self.env._('The goods must be in quarantine before a decision.'))
            if user == ret.received_by_id and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('Someone other than the person who received the goods must decide.'))
            quarantine = self.env['mos.stock.status.change'].status_location(ret.site_id, 'quarantine')
            if decision == 'restock':
                ret._move(ret.product_id, ret.qty, quarantine, ret.site_id.warehouse_id.lot_stock_id)
            else:
                ret._move(ret.product_id, ret.qty, quarantine, ret.product_id.property_stock_inventory)
            ret.write({'state': 'decided', 'decision': decision, 'decided_by_id': user.id})
            self.env['mos.audit.event'].record('customer_return_decided', ret, f'{ret.name}: {decision} {note}')

    def action_credit(self):
        """Draft the credit note for the value returned; it goes through the same approval as any credit."""
        for ret in self:
            if ret.state != 'decided' or ret.credit_note_id:
                raise UserError(self.env._('The return must be decided and not yet credited.'))
            invoice = ret.delivery_id.invoice_id
            price = invoice.unit_price if invoice else ret.product_id.list_price
            vat = invoice.vat_pct if invoice else 15.0
            note = self.env['mos.credit.note'].create(
                {
                    'customer_id': ret.customer_id.id,
                    'invoice_id': invoice.id if invoice else False,
                    'reason_id': ret.reason_id.id,
                    'amount': round(ret.qty * price * (1 + vat / 100.0), 2),
                    'note': f'{ret.name}: {ret.qty} kg returned',
                    'return_id': ret.id,
                }
            )
            ret.write({'credit_note_id': note.id})

    def action_cancel(self):
        for ret in self:
            if ret.state not in ('draft',):
                raise UserError(self.env._('Goods have been received; this return cannot be cancelled.'))
            ret.state = 'cancelled'

    def _move(self, product, qty, source, destination):
        move = self.env['stock.move'].sudo().create(
            {
                'product_id': product.id,
                'product_uom_qty': qty,
                'uom_id': product.uom_id.id,
                'location_id': source.id,
                'location_dest_id': destination.id,
                'origin': self.name,
                'quantity': qty,
                'picked': True,
            }
        )
        move._action_confirm()
        move._action_done()
        return move


class MosDeliveryOrder(models.Model):
    """Proof of delivery: the receiver signs on a single-use link the driver opens on a phone (FR-SAL-007)."""

    _inherit = 'mos.delivery.order'

    pod_token = fields.Char(readonly=True, copy=False, index=True)
    pod_token_expires = fields.Datetime(readonly=True, copy=False)
    pod_receiver = fields.Char(readonly=True)
    pod_signature = fields.Binary(readonly=True, attachment=True)
    pod_note = fields.Char(readonly=True)
    pod_signed_at = fields.Datetime(readonly=True)

    def action_pod_link(self):
        """Make the link; returns the path the driver opens."""
        self.ensure_one()
        if self.state != 'dispatched':
            raise UserError(self.env._('Proof of delivery is collected after the truck has been dispatched.'))
        if self.pod_signed_at:
            raise UserError(self.env._('This delivery has already been signed for.'))
        days = self.env['ir.config_parameter'].sudo().get_int('mos_commercial.pod_link_days', 3)
        self.sudo().write({'pod_token': secrets.token_urlsafe(24), 'pod_token_expires': fields.Datetime.now() + timedelta(days=days)})
        return f'/mos/pod/{self.pod_token}'

    @api.model
    def pod_for_token(self, token):
        if not token:
            return self.browse()
        delivery = self.sudo().search([('pod_token', '=', token)], limit=1)
        if not delivery or (delivery.pod_token_expires and delivery.pod_token_expires < fields.Datetime.now()):
            return self.browse()
        return delivery

    def record_pod(self, receiver, signature_b64, note=''):
        """Store the signature once; the link stops working at once."""
        self.ensure_one()
        if not receiver or not signature_b64:
            raise UserError(self.env._('The receiver must give a name and a signature.'))
        if self.pod_signed_at:
            raise UserError(self.env._('This delivery has already been signed for.'))
        self.sudo().write(
            {'pod_receiver': receiver.strip()[:120], 'pod_signature': signature_b64, 'pod_note': (note or '')[:200], 'pod_signed_at': fields.Datetime.now(), 'pod_token': False, 'pod_token_expires': False}
        )
        self.env['mos.audit.event'].sudo().record('pod_signed', self, f'{self.name}: received by {receiver}')


class MosException(models.Model):
    _inherit = 'mos.exception'

    kind = fields.Selection(selection_add=[('fiscal_missing', 'Fiscal receipt number missing')], ondelete={'fiscal_missing': 'cascade'})

    @api.model
    def _detectors(self):
        rows = super()._detectors()
        days = self.env['ir.config_parameter'].sudo().get_int('mos_commercial.fiscal_ref_days', 2)
        if days:
            cutoff = fields.Date.context_today(self) - timedelta(days=days)
            rows.append(('fiscal_missing', 'low', 'mos.customer.invoice', [('fiscal_receipt_ref', '=', False), ('date', '<=', cutoff)], lambda r: f'{r.name}: no fiscal receipt number after {days} days'))
        return rows
