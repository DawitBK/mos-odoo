from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


def _has(user, *groups):
    return any(user.has_group(g) for g in groups)


class MosWeighTicket(models.Model):
    """Weighing exceptions: suspicious tare, a correction after the next step, a photo that failed to upload (FRD 9.5 #4, #5, #18)."""

    _inherit = 'mos.weigh.ticket'

    _mos_editable_after_post = {'state', 'superseded_by_id', 'photo_pending', 'photo_pending_at'}

    photo_pending = fields.Boolean(readonly=True)
    photo_pending_at = fields.Datetime(readonly=True)
    tare_confirmed_by_id = fields.Many2one('res.users', readonly=True)
    reweigh_approved_by_id = fields.Many2one('res.users', readonly=True)

    def _mos_require_supervisor(self):
        if not _has(self.env.user, 'mos_flows.group_mos_gate_supervisor', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a gate supervisor can do this.'))

    def action_confirm_tare(self, note):
        """The supervisor has looked at the truck and confirms a tare that differs from its history (#5)."""
        self._mos_require_supervisor()
        if not note:
            raise UserError(self.env._('Say what was checked (water tank, driver in the cab, load left on).'))
        for ticket in self:
            ticket.sudo().write({'tare_confirmed_by_id': self.env.uid})
            self.env['mos.audit.event'].record('tare_confirmed', ticket, f'{ticket.name}: {note}')

    def action_approve_reweigh(self, note):
        """The supervisor approves a correction that goes beyond the tolerance after the next step (#4)."""
        self._mos_require_supervisor()
        if not note:
            raise UserError(self.env._('Say why the correction is right.'))
        for ticket in self:
            ticket.sudo().write({'reweigh_approved_by_id': self.env.uid})
            self.env['mos.audit.event'].record('reweigh_approved', ticket, f'{ticket.name}: {note}')

    def _mos_reweigh_needs_approval(self):
        self.ensure_one()
        old = self.supersedes_id
        if not old:
            return False
        tolerance = self.env['ir.config_parameter'].sudo().get_float('mos_flows.reweigh_tolerance_kg', 200.0)
        beyond = abs(self.gross_weight - old.gross_weight) > tolerance or abs(self.tare_weight - old.tare_weight) > tolerance
        later_step = bool(self.visit_id.sudo().inspection_ids) or self.visit_id.state in ('cleared', 'exited')
        return beyond and later_step

    def verify_blind(self, weight_gross, weight_tare):
        if self.env['mos.gate.visit']._mos_strict():
            for ticket in self:
                if ticket.tare_deviation and not ticket.tare_confirmed_by_id:
                    raise UserError(self.env._('The tare differs from this truck\'s history: a gate supervisor must confirm it before the ticket is posted.'))
                if ticket._mos_reweigh_needs_approval() and not ticket.reweigh_approved_by_id:
                    raise UserError(self.env._('This correction comes after the next step and moves the weight beyond the tolerance: a gate supervisor must approve it.'))
        result = super().verify_blind(weight_gross, weight_tare)
        for ticket in self:
            ticket.visit_id._rematch_held_invoices()
        return result

    def action_mark_photo_pending(self):
        """The weighing photo could not be uploaded now: it is allowed for a limited time and blocks invoice matching, not the receipt (#18)."""
        for ticket in self:
            ticket.sudo().write({'photo_pending': True, 'photo_pending_at': fields.Datetime.now()})

    def action_attach_photo(self, filename, content_b64):
        self.ensure_one()
        self.env['mos.attachment'].create({'name': filename, 'res_model': self._name, 'res_id': self.id, 'file': content_b64, 'note': 'Weighing photo'})
        self.sudo().write({'photo_pending': False})
        self.visit_id._rematch_held_invoices()


class MosLabInspection(models.Model):
    """Decisions can be reopened before unloading and revised after the receipt, never silently (FRD 9.5 #9, #10, #12, #20)."""

    _inherit = 'mos.lab.inspection'

    _mos_reopenable = True

    state = fields.Selection(selection_add=[('closed', 'Closed without receipt')], ondelete={'closed': 'set default'})
    partial_accept_qty = fields.Float(string='Accepted part (kg)', digits=(14, 1), help='Packaged goods in quarantine: only this much is released; the rest is blocked (#10).')
    revision_ids = fields.One2many('mos.lab.revision', 'inspection_id')

    def action_reopen(self, reason):
        """Result error found after the decision, before anything was unloaded: the decision is withdrawn for re-entry (#20)."""
        if not _has(self.env.user, 'mos_lab.group_mos_lab_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a lab manager can reopen a decision.'))
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        for insp in self:
            if insp.state != 'decided':
                raise UserError(self.env._('The inspection is not decided.'))
            visit = insp.visit_id
            if visit.grn_state != 'none' or visit.pv_draft_id or (visit.flow == 'B' and visit.received_qty):
                raise UserError(self.env._('Stock has been received on this decision: use a decision revision with approval.'))
            insp.with_context(mos_allow_reopen=True).sudo().write(
                {'state': 'tested', 'decision': False, 'deduction_pct': 0.0, 'price_adjust_pct': 0.0, 'decision_note': False, 'decided_by_id': False}
            )
            self.env['mos.audit.event'].record('decision_reopened', insp, f'{insp.name}: {reason}')

    def action_request_revision(self, new_decision, new_deduction_pct, note):
        """A disputed decision or a result error after the receipt: a superseding decision that another lab manager approves (#12, #20)."""
        if not _has(self.env.user, 'mos_lab.group_mos_lab_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a lab manager can ask for a revision.'))
        self.ensure_one()
        return self.env['mos.lab.revision'].create(
            {'inspection_id': self.id, 'new_decision': new_decision, 'new_deduction_pct': new_deduction_pct, 'note': note}
        )


class MosLabRevision(models.Model):
    """A superseding usage decision with its financial consequence: the on-hand share is revalued by the change in payable quantity
    and the supplier is debited (more deduction) or flagged for credit (less deduction)."""

    _name = 'mos.lab.revision'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS decision revision'
    _order = 'id desc'
    _mos_final_states = ('applied', 'rejected')

    name = fields.Char(readonly=True, copy=False, default='New')
    inspection_id = fields.Many2one('mos.lab.inspection', required=True, ondelete='restrict')
    old_decision = fields.Selection([('accept', 'Accept'), ('accept_deduction', 'Accept with deduction'), ('reject', 'Reject')], readonly=True)
    old_deduction_pct = fields.Float(readonly=True)
    old_payable_qty = fields.Float(readonly=True, digits=(14, 1))
    new_decision = fields.Selection([('accept', 'Accept'), ('accept_deduction', 'Accept with deduction'), ('reject', 'Reject')], required=True)
    new_deduction_pct = fields.Float()
    note = fields.Char(required=True, string='Why (third-party test, result error...)')
    requested_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    approved_by_id = fields.Many2one('res.users', readonly=True)
    delta_kg = fields.Float(readonly=True, digits=(14, 1), help='Change in payable quantity: positive when the supplier is owed more.')
    debit_note_id = fields.Many2one('mos.debit.note', readonly=True)
    state = fields.Selection([('requested', 'Requested'), ('applied', 'Applied'), ('rejected', 'Rejected')], default='requested', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            insp = self.env['mos.lab.inspection'].browse(vals['inspection_id'])
            if insp.state != 'decided':
                raise UserError(self.env._('Only a decided inspection can be revised.'))
            vals.setdefault('old_decision', insp.decision)
            vals['old_deduction_pct'] = insp.deduction_pct
            vals['old_payable_qty'] = insp.payable_qty
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.lab.revision')
        return super().create(vals_list)

    def action_approve(self):
        user = self.env.user
        if not _has(user, 'mos_lab.group_mos_lab_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a lab manager can approve a revision.'))
        for rev in self:
            if rev.state != 'requested':
                raise UserError(self.env._('This revision is closed.'))
            if user == rev.requested_by_id or user == rev.inspection_id.decided_by_id:
                raise UserError(self.env._('Someone other than the person who decided and the person who asked must approve the revision.'))
            insp = rev.inspection_id
            insp.with_context(mos_allow_reopen=True).sudo().write(
                {
                    'decision': rev.new_decision,
                    'deduction_pct': 0.0 if rev.new_decision != 'accept_deduction' else rev.new_deduction_pct,
                    'decision_note': rev.note,
                }
            )
            insp.invalidate_recordset(['payable_qty'])
            delta = rev.old_payable_qty - insp.payable_qty
            note = False
            order = insp.visit_id.sudo().purchase_id
            invoice = self.env['mos.supplier.invoice'].sudo().search([('purchase_id', '=', order.id), ('state', 'in', ('payable', 'vouchered'))], order='id desc', limit=1)
            lines = order.order_line.filtered(lambda line: line.product_id == insp.product_id)
            price = lines[:1].price_unit
            if delta > 0.05 and invoice:
                amount = round(delta * price * (1 + invoice.vat_pct / 100.0), 2)
                note = self.env['mos.debit.note'].sudo().create({'invoice_id': invoice.id, 'supplier_id': invoice.supplier_id.id, 'amount': amount, 'reason': self.env._('Revised quality decision %s', rev.name)})
            elif abs(delta) > 0.05:
                self.env['mos.exception'].raise_for(
                    'receipt_exception',
                    rev,
                    self.env._('%(name)s moves the payable quantity by %(delta)s kg: adjust the supplier account', name=rev.name, delta=round(-delta, 1)),
                    severity='high',
                )
            rev.write({'state': 'applied', 'approved_by_id': user.id, 'delta_kg': delta, 'debit_note_id': note.id if note else False})
            self.env['mos.audit.event'].record('decision_revised', insp, f'{insp.name}: {rev.old_decision}/{rev.old_deduction_pct} -> {rev.new_decision}/{rev.new_deduction_pct}, payable {rev.old_payable_qty} -> {insp.payable_qty}')

    def action_reject(self):
        for rev in self:
            if rev.state != 'requested':
                raise UserError(self.env._('This revision is closed.'))
            rev.state = 'rejected'


class MosLabSample(models.Model):
    """A lost or mislabelled sample is replaced by a retest sample, and the results taken from it are invalidated (FRD 9.5 #19)."""

    _inherit = 'mos.lab.sample'

    state = fields.Selection(selection_add=[('lost', 'Lost or mislabelled')], ondelete={'lost': 'set default'})
    retest_of_id = fields.Many2one('mos.lab.sample', readonly=True)
    lost_reason = fields.Char(readonly=True)

    def action_mark_lost(self, reason, invalidate_results=True):
        if not reason:
            raise UserError(self.env._('Give the reason.'))
        retests = self.browse()
        for sample in self:
            if sample.state != 'retained':
                raise UserError(self.env._('Only a retained sample can be marked lost.'))
            sample.sudo().write({'state': 'lost', 'lost_reason': reason})
            retest = self.sudo().create({'inspection_id': sample.inspection_id.id, 'location': sample.location, 'retest_of_id': sample.id})
            retests |= retest
            insp = sample.inspection_id.sudo()
            if invalidate_results and insp.state != 'decided':
                insp.result_ids.filtered('valid').write({'valid': False, 'invalid_cause': self.env._('Sample %(name)s lost: %(reason)s', name=sample.name, reason=reason)})
                insp.write({'state': 'open'})
            self.env['mos.audit.event'].record('sample_lost', sample, f'{sample.name} -> {retest.name}: {reason}')
        return retests


class MosSupplierInvoice(models.Model):
    """Matching waits for receipt, quality decision and weight or photo clearance, and prices follow the quality adjustment (FRD 9.5 #23-#26, #33)."""

    _inherit = 'mos.supplier.invoice'

    match_state = fields.Selection(
        selection_add=[('awaiting_receipt', 'Awaiting receipt'), ('qc_hold', 'Quality hold'), ('payment_hold', 'Payment hold')],
        ondelete={'awaiting_receipt': 'set default', 'qc_hold': 'set default', 'payment_hold': 'set default'},
    )

    def _mos_visits(self, order):
        return self.env['mos.gate.visit'].sudo().search([('purchase_id', '=', order.id)])

    def _mos_expected_price(self, order, product, base_price):
        inspections = self._mos_visits(order).inspection_ids.filtered(lambda i: i.state == 'decided' and i.decision != 'reject' and i.product_id == product)
        total = sum(inspections.mapped('payable_qty'))
        if not total:
            return base_price
        adjust = sum(i.price_adjust_pct * i.payable_qty for i in inspections) / total
        return round(base_price * (1 + adjust / 100.0), 4)

    def _mos_match_wait(self, order, product):
        if order.currency_id and order.currency_id != order.company_id.currency_id:
            raise UserError(self.env._('The order is in %(po)s but invoices are matched in %(co)s: convert the order first.', po=order.currency_id.name, co=order.company_id.currency_id.name))
        visits = self._mos_visits(order)
        if any(v.payment_blocked for v in visits):
            return ('payment_hold', self.env._('a receipt has an estimated tare nobody has accepted yet'))
        hours = self.env['ir.config_parameter'].sudo().get_int('mos_flows.photo_pending_max_hours', 4)
        stale = fields.Datetime.now() - timedelta(hours=hours)
        if any(t.photo_pending and t.photo_pending_at < stale for t in visits.ticket_ids):
            return ('payment_hold', self.env._('a weighing photo is still missing'))
        if any(i.state != 'decided' for i in visits.inspection_ids if i.product_id == product and i.state != 'closed'):
            return ('qc_hold', self.env._('the goods are still in quality hold'))
        moves = order.picking_ids.move_ids.filtered(lambda m: m.state == 'done' and m.product_id == product and m.picking_type_id.code == 'incoming')
        if not sum(moves.mapped('quantity')):
            return ('awaiting_receipt', self.env._('nothing has been received yet'))
        return None

    def _mos_quality_deduction_kg(self, order, product):
        total = 0.0
        for insp in self._mos_visits(order).inspection_ids.filtered(lambda i: i.state == 'decided' and i.decision != 'reject' and i.product_id == product):
            base = insp.visit_id.received_qty if (insp.visit_id.flow == 'B' and insp.visit_id.received_qty) else insp.net_weight
            total += base - insp.payable_qty
        return round(total, 1)


class MosPaymentVoucher(models.Model):
    _inherit = 'mos.payment.voucher'

    approved_bank_digest = fields.Char(readonly=True, copy=False)

    def _mos_bank_digest(self):
        self.ensure_one()
        import hashlib

        return hashlib.sha256((self.supplier_id.sudo().mos_bank_account_enc or '').encode()).hexdigest()

    def _mos_stamp_bank(self):
        for voucher in self.filtered(lambda v: v.state == 'approved'):
            voucher.sudo().write({'approved_bank_digest': voucher._mos_bank_digest()})

    def action_submit(self):
        result = super().action_submit()
        self._mos_stamp_bank()
        return result

    def _mos_approval_done(self, request):
        super()._mos_approval_done(request)
        self._mos_stamp_bank()

    @api.depends('payment_ids.amount', 'payment_ids.reversal_ids.state', 'net_amount')
    def _compute_paid(self):
        for v in self:
            live = v.payment_ids.filtered(lambda p: not p.reversal_ids)
            v.paid_amount = sum(live.mapped('amount'))
            v.remaining_amount = round(v.net_amount - v.paid_amount, 2)


class MosPayment(models.Model):
    """Payment refusals that follow from changes after approval, and a reversal that voids the withholding certificate (FRD 9.5 #29-#31, #34)."""

    _inherit = 'mos.payment'

    reversal_ids = fields.One2many('mos.payment.reversal', 'payment_id')
    certificate_ids = fields.One2many('mos.wht.certificate', 'payment_id')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            voucher = self.env['mos.payment.voucher'].browse(vals['voucher_id'])
            supplier = voucher.supplier_id
            if supplier.mos_supplier_state == 'blocked':
                raise UserError(self.env._('%s is blocked: no payment can be recorded.', supplier.display_name))
            if voucher.approved_bank_digest and voucher.approved_bank_digest != voucher._mos_bank_digest():
                raise UserError(self.env._("%s's payment account changed after this voucher was approved: cancel it and raise a new voucher so it is approved again.", supplier.display_name))
        payments = super().create(vals_list)
        for pay in payments:
            voucher = pay.voucher_id.sudo()
            share = (pay.amount / voucher.net_amount) if voucher.net_amount else 0.0
            self.env['mos.wht.certificate'].sudo().create(
                {'payment_id': pay.id, 'supplier_id': pay.supplier_id.id, 'base_amount': round(voucher.gross_amount * share, 2), 'wht_amount': round(voucher.wht_amount * share, 2)}
            )
        return payments


class MosWhtCertificate(models.Model):
    """What was withheld from a payment, with a gap-free number that survives its own voiding (FRD 9.5 #31, #34)."""

    _name = 'mos.wht.certificate'
    _inherit = ['mos.posted']
    _description = 'MOS withholding certificate'
    _order = 'id desc'
    _mos_final_states = ('issued', 'void')
    _mos_editable_after_post = {'state', 'void_reason', 'voided_by_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    payment_id = fields.Many2one('mos.payment', required=True, ondelete='restrict')
    supplier_id = fields.Many2one('res.partner', required=True)
    base_amount = fields.Float(readonly=True)
    wht_amount = fields.Float(string='Withheld', readonly=True)
    state = fields.Selection([('issued', 'Issued'), ('void', 'Void')], default='issued', required=True, readonly=True)
    void_reason = fields.Char(readonly=True)
    voided_by_id = fields.Many2one('res.users', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.wht.certificate')
        return super().create(vals_list)


class MosPaymentReversal(models.Model):
    """A payment recorded wrongly is reversed by a document, never edited: the allocation is undone, the withholding certificate is
    voided (its number kept), a cheque is voided, and the payment can be recorded again (FRD 9.5 #31, #34)."""

    _name = 'mos.payment.reversal'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS payment reversal'
    _order = 'id desc'
    _mos_final_states = ('posted',)

    name = fields.Char(readonly=True, copy=False, default='New')
    payment_id = fields.Many2one('mos.payment', required=True, ondelete='restrict')
    amount = fields.Float(related='payment_id.amount', store=True)
    reason = fields.Char(required=True)
    reversed_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)

    _payment_once = models.Constraint('unique(payment_id)', 'A payment can be reversed only once.')

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        if not _has(user, 'mos_approvals.group_mos_finance_manager', 'mos_core.group_mos_admin'):
            raise UserError(self.env._('Only a finance manager can reverse a payment.'))
        for vals in vals_list:
            payment = self.env['mos.payment'].browse(vals['payment_id'])
            if user.id == payment.create_uid.id and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('Someone other than the person who recorded the payment must reverse it.'))
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.payment.reversal')
        reversals = super().create(vals_list)
        for rev in reversals:
            payment = rev.payment_id.sudo()
            payment.certificate_ids.filtered(lambda c: c.state == 'issued').write({'state': 'void', 'void_reason': rev.reason, 'voided_by_id': self.env.uid})
            if payment.cheque_leaf_id:
                payment.cheque_leaf_id.write({'state': 'void', 'void_reason': self.env._('Payment %s reversed', payment.name)})
            voucher = payment.voucher_id
            voucher.invalidate_recordset(['paid_amount', 'remaining_amount'])
            if voucher.state == 'paid':
                voucher.write({'state': 'partially_paid' if voucher.paid_amount > 0 else 'approved'})
                voucher.line_ids.invoice_id.write({'state': 'vouchered'})
            voucher._refresh_state()
            self.env['mos.audit.event'].record('payment_reversed', rev, f'{rev.name}: {payment.name} {payment.amount}: {rev.reason}')
        return reversals
