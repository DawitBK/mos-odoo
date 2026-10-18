from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestScenarios(TransactionCase):
    """The failure and exception cases of FRD section 9.5, one test each (or two when they come in pairs)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLS', 'warehouse_id': warehouse.id})
        cls.stock = warehouse.lot_stock_id

        def user(login, groups):
            u = new_test_user(env, login=login, groups=groups)
            u.mos_site_ids = [Command.set(cls.site.ids)]
            return u

        cls.guard = user('sguard', 'mos_gate.group_mos_gate_guard')
        cls.supervisor = user('ssuper', 'mos_flows.group_mos_gate_supervisor')
        cls.verifier = user('sverifier', 'mos_gate.group_mos_gate_verifier')
        cls.analyst = user('sanalyst', 'mos_lab.group_mos_lab_analyst')
        cls.manager = user('smanager', 'mos_lab.group_mos_lab_manager')
        cls.manager2 = user('smanager2', 'mos_lab.group_mos_lab_manager')
        cls.store = user('sstore', 'mos_inbound.group_mos_storekeeper')
        cls.buyer = user('sbuyer', 'mos_procurement.group_mos_procurement_user')
        cls.pmanager = user('spmanager', 'mos_procurement.group_mos_procurement_manager')
        cls.apclerk = user('sap', 'mos_payables.group_mos_ap_clerk')
        cls.cashier = user('scashier', 'mos_payables.group_mos_cashier')
        cls.finance = user('sfinance', 'mos_approvals.group_mos_finance_manager')
        uom = env.ref('uom.product_uom_kgm')
        cls.wheat = env['product.product'].create({'name': 'Scenario wheat', 'type': 'consu', 'is_storable': True, 'uom_id': uom.id, 'standard_price': 20.0})
        cls.barley = env['product.product'].create({'name': 'Scenario barley', 'type': 'consu', 'is_storable': True, 'uom_id': uom.id, 'standard_price': 15.0})
        cls.supplier = env['res.partner'].create({'name': 'Scenario Supplier', 'supplier_rank': 1, 'mos_vat_pct': 15.0, 'mos_wht_pct': 2.0})
        cls.specs = {}
        for product, limit in ((cls.wheat, 13.5), (cls.barley, 12.0)):
            spec = env['mos.lab.spec'].create({'product_id': product.id, 'line_ids': [Command.create({'name': 'Moisture %', 'min_value': 0, 'max_value': limit})]})
            spec.action_approve()
            cls.specs[product] = spec
        cls.account = env['mos.paying.account'].create({'name': 'Scenario bank', 'kind': 'bank'})

    # -- helpers ---------------------------------------------------------------------------------------------------
    def _order(self, qty=40000.0, product=None):
        product = product or self.wheat
        order = self.env['purchase.order'].create({'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': product.id, 'product_qty': qty, 'price_unit': product.standard_price})]})
        order.button_confirm()
        return order

    def _qty(self, location, product=None):
        quants = self.env['stock.quant'].search([('product_id', '=', (product or self.wheat).id), ('location_id', '=', location.id)])
        return sum(quants.mapped('quantity'))

    def _weighed(self, order, plate, gross=50000.0, tare=15000.0, **extra):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': plate, 'purchase_id': order.id, 'partner_id': self.supplier.id, **extra})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(gross)
        ticket.record_second(tare)
        ticket.with_user(self.verifier).verify_blind(gross, tare)
        return visit

    def _decided(self, visit, moisture=12.0, decision='accept', deduction=0.0, note=''):
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(insp.spec_id.line_ids, moisture)
        insp.action_submit_results()
        insp.with_user(self.manager).decide(decision, deduction, note)
        return insp

    def _received(self, order, plate, **kw):
        visit = self._weighed(order, plate, **kw)
        self._decided(visit)
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        return visit

    def _strict(self, on=True):
        self.env['ir.config_parameter'].sudo().set_str('mos_flows.strict_controls', 'True' if on else 'False')

    # -- #1, #2: no purchase order and a wrong one -----------------------------------------------------------------
    def test_visit_without_a_purchase_order_is_on_hold_until_one_is_linked(self):
        self._strict()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'S-1'})
        with self.assertRaises(UserError):
            visit.action_start_weighing()
        order = self._order()
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_link_po(order, 'x')  # a guard is not a buyer
        with self.assertRaises(UserError):
            visit.with_user(self.buyer).action_link_po(order, '')
        visit.with_user(self.buyer).action_link_po(order, 'PO found on the supplier delivery note')
        self.assertEqual(visit.purchase_id, order)
        visit.action_start_weighing()
        self.assertEqual(visit.state, 'weighing')

    def test_relinking_the_order_re_resolves_the_specification(self):
        wheat_order, barley_order = self._order(), self._order(product=self.barley)
        visit = self._weighed(wheat_order, 'S-2')
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(insp.spec_id.line_ids, 12.0)
        insp.action_submit_results()
        visit.with_user(self.buyer).action_link_po(barley_order, 'Wrong order on the paperwork')
        insp.invalidate_recordset()
        self.assertEqual((insp.product_id, insp.spec_id), (self.barley, self.specs[self.barley]))
        self.assertEqual(insp.state, 'open')
        self.assertFalse(insp.result_ids.filtered('valid'), 'results taken against the old specification are invalidated')
        self.assertEqual(insp.suggested_outcome, 'incomplete')

    def test_reject_at_the_gate(self):
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'S-3'})
        with self.assertRaises(UserError):
            visit.action_reject_at_gate('')
        visit.with_user(self.guard).action_reject_at_gate('No order and no documents')
        self.assertEqual(visit.state, 'cancelled')
        self.assertEqual(visit.incident_ids.kind, 'rejection')

    # -- #4, #5: weighing corrections and a suspicious tare ----------------------------------------------------------
    def test_suspicious_tare_needs_a_supervisor_before_the_ticket_posts(self):
        self._strict()
        self.env['mos.gate.vehicle'].create({'plate': 'S-4', 'stored_tare': 14000.0})
        order = self._order()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'S-4', 'purchase_id': order.id})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(50000.0)
        ticket.record_second(16500.0)  # 2.5 t above the history
        with self.assertRaises(UserError):
            ticket.with_user(self.verifier).verify_blind(50000.0, 16500.0)
        with self.assertRaises(UserError):
            ticket.with_user(self.guard).action_confirm_tare('looked')  # not a supervisor
        ticket.with_user(self.supervisor).action_confirm_tare('Water tank was empty, driver out of the cab')
        ticket.with_user(self.verifier).verify_blind(50000.0, 16500.0)
        self.assertEqual(ticket.state, 'posted')

    def test_correction_after_the_next_step_beyond_tolerance_needs_approval(self):
        self._strict()
        order = self._order()
        visit = self._weighed(order, 'S-5')
        insp = visit.with_user(self.analyst).action_open_inspection()
        reason = self.env['mos.reason'].create({'domain': 'reweigh', 'code': 'TYPO', 'name': 'Typing error'})
        new = visit.ticket_id.with_user(self.guard).reweigh(reason)
        new.record_first(48000.0)  # 2 t away from the 50 t first entry
        new.record_second(15000.0)
        with self.assertRaises(UserError):
            new.with_user(self.verifier).verify_blind(48000.0, 15000.0)
        new.with_user(self.supervisor).action_approve_reweigh('Scale ticket photographed and checked')
        new.with_user(self.verifier).verify_blind(48000.0, 15000.0)
        self.assertEqual(new.state, 'posted')
        self.assertTrue(insp)

    # -- #6, #7: leaves without tare, leaves without unloading --------------------------------------------------------
    def test_exit_without_second_weighing_blocks_payment_until_released(self):
        order = self._order()
        visit = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'S-6', 'purchase_id': order.id})
        visit.action_start_weighing()
        visit.ticket_id.record_first(46000.0)
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_exit_without_tare('left', 14000.0)
        visit.with_user(self.supervisor).action_exit_without_tare('Driver left during the shift change', 14500.0)
        self.assertEqual(visit.state, 'exited')
        self.assertTrue(visit.tare_estimated and visit.payment_blocked)
        self.assertEqual(visit.ticket_id.net_weight, 31500.0)
        self.assertTrue(self.env['mos.exception'].search([('kind', '=', 'receipt_exception'), ('res_id', '=', visit.id)]))
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': 'S6-1', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': 31500.0, 'unit_price': 20.0}
        )
        invoice.action_match()
        self.assertEqual(invoice.match_state, 'payment_hold')
        with self.assertRaises(UserError):
            visit.with_user(self.buyer).action_release_payment_block('ok')
        visit.with_user(self.pmanager).action_release_payment_block('Estimated tare accepted by the purchasing manager')
        self.assertFalse(visit.payment_blocked)
        self.assertNotEqual(invoice.match_state, 'payment_hold', 'the invoice is matched again when the block lifts')

    def test_accepted_truck_that_leaves_without_unloading_is_abandoned(self):
        order = self._order()
        visit = self._weighed(order, 'S-7')
        insp = visit.with_user(self.analyst).action_open_inspection()
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_abandon('gone')
        visit.with_user(self.supervisor).action_abandon('Left through the side gate')
        self.assertEqual(visit.state, 'abandoned')
        self.assertEqual(insp.state, 'closed')
        reopened = self.env['mos.gate.visit'].with_user(self.guard).create({'site_id': self.site.id, 'plate': 'S-7'})
        self.assertTrue(reopened, 'the plate can arrive again: the abandoned visit no longer counts as open')

    # -- #10, #11: partial acceptance and weight disputes -------------------------------------------------------------
    def test_weight_dispute_uses_the_agreed_basis(self):
        order = self._order()
        visit = self._weighed(order, 'S-8', declared_qty=34000.0)
        self.assertEqual(visit.weight_variance, 1000.0)
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_resolve_weight_dispute('declared', 'agreed')
        visit.with_user(self.pmanager).action_resolve_weight_dispute('declared', 'Supplier and mill agreed on the delivery note weight')
        insp = self._decided(visit)
        self.assertEqual(insp.payable_qty, 34000.0, 'the receipt follows the agreed basis, not the bridge')
        self.assertEqual(visit.weight_variance, 1000.0, 'the variance stays on record')

    # -- #3, #32: reversing a receipt --------------------------------------------------------------------------------
    def test_receipt_reversal_is_blocked_by_a_payable_invoice(self):
        order = self._order()
        visit = self._received(order, 'S-9')
        self.assertEqual(self._qty(self.stock), 35000.0)
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': 'S9-1', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': 35000.0, 'unit_price': 20.0}
        )
        invoice.action_match()
        self.assertEqual(invoice.state, 'payable')
        with self.assertRaises(UserError):
            visit.with_user(self.pmanager).action_reverse_grn('wrong order')
        invoice.voucher_line_ids.voucher_id.action_cancel()  # the voucher drafted for it
        invoice.action_cancel()
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_reverse_grn('x')
        visit.with_user(self.pmanager).action_reverse_grn('Booked against the wrong order')
        self.assertEqual(visit.grn_state, 'none')
        self.assertEqual(self._qty(self.stock), 0.0)
        self.assertEqual(visit.pv_draft_id.state, 'reversed')

    # -- #13, #14: over- and under-delivery -------------------------------------------------------------------------
    def test_excess_can_be_made_to_need_a_manager(self):
        policy = self.env['mos.receipt.policy'].search([('product_id', '=', False)])
        policy.over_action = 'approve'
        order = self._order(qty=30000.0)
        visit = self._weighed(order, 'S-10')  # 35 t on a 30 t order
        self._decided(visit)
        with self.assertRaises(UserError):
            visit.with_user(self.guard).action_prepare_grn()
        with self.assertRaises(UserError):
            visit.with_user(self.buyer).action_approve_excess('ok')
        visit.with_user(self.pmanager).action_approve_excess('Supplier delivered the next lot early')
        visit.with_user(self.guard).action_prepare_grn()
        self.assertEqual(visit.grn_state, 'prepared')

    def test_part_delivery_keeps_the_order_open_and_a_small_shortfall_closes_it(self):
        order = self._order(qty=40000.0)
        self._received(order, 'S-11')  # 35 t of 40 t
        backorder = order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
        self.assertTrue(backorder, 'the 5 t still owed stays open as a backorder')
        self.assertEqual(sum(backorder.move_ids.mapped('product_uom_qty')), 5000.0)
        second = self._order(qty=36000.0)
        self._received(second, 'S-12')  # 35 t of 36 t: 2.8 % short, inside the 5 % tolerance
        self.assertFalse(second.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')), 'a shortfall inside the tolerance closes the line')

    # -- #12, #19, #20: decisions and samples -------------------------------------------------------------------------
    def test_a_decision_can_be_reopened_only_before_anything_is_received(self):
        order = self._order()
        visit = self._weighed(order, 'S-13')
        insp = self._decided(visit, moisture=14.0, decision='accept', note='concession')
        with self.assertRaises(UserError):
            insp.with_user(self.analyst).action_reopen('error')  # not a manager
        insp.with_user(self.manager).action_reopen('The moisture meter was mis-calibrated')
        self.assertEqual((insp.state, insp.decision), ('tested', False))
        insp.with_user(self.manager).decide('accept', 0.0, 'concession')
        visit.with_user(self.guard).action_prepare_grn()
        with self.assertRaises(UserError):
            insp.with_user(self.manager).action_reopen('too late')

    def test_revision_after_the_receipt_needs_another_manager_and_debits_the_supplier(self):
        order = self._order()
        visit = self._received(order, 'S-14')
        insp = visit.inspection_id
        invoice = self.env['mos.supplier.invoice'].with_user(self.apclerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': 'S14-1', 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': 35000.0, 'unit_price': 20.0}
        )
        invoice.action_match()
        rev = insp.with_user(self.manager).action_request_revision('accept_deduction', 3.0, 'Third-party laboratory found more foreign matter')
        with self.assertRaises(UserError):
            rev.with_user(self.manager).action_approve()  # the manager decided
        rev.with_user(self.manager2).action_approve()
        self.assertEqual(rev.state, 'applied')
        self.assertEqual(insp.payable_qty, 33950.0)
        self.assertEqual(rev.delta_kg, 1050.0)
        self.assertEqual(rev.debit_note_id.amount, round(1050.0 * 20.0 * 1.15, 2))
        with self.assertRaises(UserError):
            rev.write({'note': 'edited'})

    def test_lost_sample_is_replaced_and_its_results_invalidated(self):
        order = self._order()
        visit = self._weighed(order, 'S-15')
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(insp.spec_id.line_ids, 12.0)
        insp.action_submit_results()
        sample = insp.sample_ids
        retest = sample.with_user(self.analyst).action_mark_lost('Label came off in the oven')
        self.assertEqual(sample.state, 'lost')
        self.assertEqual(retest.retest_of_id, sample)
        self.assertEqual(insp.state, 'open')
        self.assertFalse(insp.result_ids.filtered('valid'))

    # -- #23-#26, #33: matching ------------------------------------------------------------------------------------
    def _invoice(self, order, qty, price=None, ref='M-1'):
        return self.env['mos.supplier.invoice'].with_user(self.apclerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': ref, 'purchase_id': order.id, 'product_id': self.wheat.id, 'qty': qty, 'unit_price': price or 20.0}
        )

    def test_invoice_before_the_receipt_waits_and_matches_when_the_goods_arrive(self):
        order = self._order()
        invoice = self._invoice(order, 35000.0, ref='M-2')
        invoice.action_match()
        self.assertEqual(invoice.match_state, 'awaiting_receipt')
        self._received(order, 'S-16')
        self.assertEqual((invoice.match_state, invoice.state), ('matched', 'payable'))

    def test_invoice_while_the_lab_has_not_decided_is_held(self):
        order = self._order()
        visit = self._weighed(order, 'S-17')
        visit.with_user(self.analyst).action_open_inspection()
        invoice = self._invoice(order, 35000.0, ref='M-3')
        invoice.action_match()
        self.assertEqual(invoice.match_state, 'qc_hold')

    def test_billing_the_quality_deduction_is_credited_automatically(self):
        order = self._order()
        self._received(order, 'S-18')
        self.assertEqual(order.picking_ids.move_ids.filtered(lambda m: m.state == 'done').quantity, 35000.0)
        order2 = self._order()
        visit = self._weighed(order2, 'S-19')
        self._decided(visit, moisture=13.0, decision='accept_deduction', deduction=2.0, note='Slight dust')
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        invoice = self._invoice(order2, 35000.0, ref='M-4')  # billed for the full net weight
        invoice.action_match()
        self.assertEqual((invoice.match_state, invoice.state), ('matched', 'payable'))
        self.assertEqual(invoice.debit_note_ids.amount, round(700.0 * 20.0 * 1.15, 2))

    def test_price_follows_the_quality_adjustment(self):
        spec = self.specs[self.wheat]
        spec_new = spec.action_new_version()
        self.env['mos.lab.deduction.rule'].create({'line_id': spec_new.line_ids.id, 'from_value': 13.0, 'to_value': 13.5, 'price_adjust_pct': -5.0})
        spec_new.action_approve()
        order = self._order()
        visit = self._weighed(order, 'S-20')
        insp = visit.with_user(self.analyst).action_open_inspection().with_user(self.analyst)
        insp.record_result(insp.spec_id.line_ids, 13.2)
        insp.action_submit_results()
        insp.with_user(self.manager).decide('accept')
        visit.with_user(self.guard).action_prepare_grn()
        visit.with_user(self.store).action_confirm_grn()
        at_order_price = self._invoice(order, 35000.0, ref='M-5')
        at_order_price.action_match()
        self.assertEqual(at_order_price.match_state, 'exception', 'billing the full price for stock that lost 5 % of its price')
        self.assertEqual(at_order_price.ordered_price, 19.0)
        adjusted = self._invoice(order, 35000.0, price=19.0, ref='M-6')
        adjusted.action_match()
        self.assertEqual(adjusted.match_state, 'matched')

    def test_photo_pending_holds_matching_after_the_grace_period(self):
        order = self._order()
        visit = self._received(order, 'S-21')
        ticket = visit.ticket_id
        ticket.with_user(self.guard).action_mark_photo_pending()
        ticket.sudo().photo_pending_at = fields.Datetime.now() - timedelta(hours=5)
        invoice = self._invoice(order, 35000.0, ref='M-7')
        invoice.action_match()
        self.assertEqual(invoice.match_state, 'payment_hold', 'a photo missing beyond the grace period holds matching')
        ticket.sudo().photo_pending = False
        invoice.action_match()
        self.assertEqual(invoice.match_state, 'matched')
        fresh = self._received(self._order(), 'S-21B')
        fresh.ticket_id.with_user(self.guard).action_mark_photo_pending()
        order2 = fresh.purchase_id
        second = self._invoice(order2, 35000.0, ref='M-8')
        second.action_match()
        self.assertEqual(second.match_state, 'matched', 'inside the grace period the photo does not block matching')

    # -- #29-#31, #34: payments -----------------------------------------------------------------------------------
    def _voucher(self, order, ref):
        invoice = self._invoice(order, 35000.0, ref=ref)
        invoice.action_match()
        voucher = invoice.voucher_line_ids.voucher_id  # drafted for the payables clerk when the invoice matched
        self.assertEqual(voucher.state, 'draft')
        voucher.with_user(self.apclerk).action_submit()
        self.assertEqual(voucher.state, 'approved')
        return voucher

    def test_payment_reversal_voids_the_certificate_and_reopens_the_voucher(self):
        order = self._order()
        self._received(order, 'S-22')
        voucher = self._voucher(order, 'P-1')
        payment = self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': voucher.net_amount, 'account_id': self.account.id})
        self.assertEqual(voucher.state, 'paid')
        cert = payment.certificate_ids
        self.assertEqual(len(cert), 1)
        self.assertRegex(cert.name, r'^WHT-\d{4}-\d{5}$')
        self.assertEqual(cert.wht_amount, voucher.wht_amount)
        with self.assertRaises(UserError):
            self.env['mos.payment.reversal'].with_user(self.cashier).create({'payment_id': payment.id, 'reason': 'wrong account'})  # not finance
        reversal = self.env['mos.payment.reversal'].with_user(self.finance).create({'payment_id': payment.id, 'reason': 'Paid from the wrong bank account'})
        self.assertRegex(reversal.name, r'^PR-\d{4}-\d{5}$')
        self.assertEqual((cert.state, cert.name[:4]), ('void', 'WHT-'), 'the certificate is voided and keeps its number')
        self.assertEqual(voucher.state, 'approved')
        self.assertEqual(voucher.remaining_amount, voucher.net_amount)
        again = self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': voucher.net_amount, 'account_id': self.account.id})
        self.assertEqual(voucher.state, 'paid')
        self.assertNotEqual(again.certificate_ids.name, cert.name, 'a new certificate is issued')
        with self.assertRaises(UserError):
            reversal.write({'reason': 'edited'})

    def test_payment_refused_for_a_blocked_supplier_or_a_changed_account(self):
        order = self._order()
        self._received(order, 'S-23')
        voucher = self._voucher(order, 'P-2')
        self.supplier.sudo().mos_supplier_state = 'blocked'
        with self.assertRaises(UserError):
            self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 1000.0, 'account_id': self.account.id})
        self.supplier.sudo().mos_supplier_state = 'candidate'
        change = self.env['mos.supplier.bank.change'].with_user(self.buyer).create({'partner_id': self.supplier.id, 'new_account': '1000555566667', 'reason': 'Moved banks'})
        change.with_user(self.pmanager).action_verify('Called the finance officer on file')
        change.with_user(self.finance).action_apply()
        with self.assertRaises(UserError):
            self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 1000.0, 'account_id': self.account.id})
