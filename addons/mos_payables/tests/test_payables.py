from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestPayables(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.clerk = new_test_user(env, login='pay_clerk', groups='mos_payables.group_mos_ap_clerk')
        cls.cashier = new_test_user(env, login='pay_cashier', groups='mos_payables.group_mos_cashier')
        cls.fin = new_test_user(env, login='pay_fin', groups='mos_approvals.group_mos_finance_manager')
        cls.supplier = env['res.partner'].create({'name': 'Highland Union', 'supplier_rank': 1, 'mos_vat_pct': 15.0, 'mos_wht_pct': 2.0})
        cls.product = env['product.product'].create(
            {'name': 'Durum', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 10}
        )
        cls.account = env['mos.paying.account'].create({'name': 'Main bank', 'kind': 'bank'})

    def _received_po(self, qty=40000, price=10.0, received=40000):
        po = self.env['purchase.order'].create(
            {'partner_id': self.supplier.id, 'order_line': [Command.create({'product_id': self.product.id, 'product_qty': qty, 'price_unit': price})]}
        )
        po.button_confirm()
        picking = po.picking_ids
        picking.move_ids.write({'quantity': received, 'picked': True})
        picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
        return po

    def _invoice(self, po, qty, price, ref='INV-001'):
        return self.env['mos.supplier.invoice'].with_user(self.clerk).create(
            {'supplier_id': self.supplier.id, 'supplier_ref': ref, 'purchase_id': po.id, 'product_id': self.product.id, 'qty': qty, 'unit_price': price}
        )

    def test_amounts_use_the_suppliers_tax_profile(self):
        inv = self._invoice(self._received_po(), 40000, 10.0)
        self.assertEqual(inv.net_amount, 400000.0)
        self.assertEqual(inv.vat_amount, 60000.0)
        self.assertEqual(inv.gross_amount, 460000.0)
        self.assertEqual(inv.wht_amount, 8000.0)
        self.assertEqual(inv.payable_amount, 452000.0)

    def test_matching_invoice_is_payable(self):
        inv = self._invoice(self._received_po(), 40000, 10.0)
        inv.action_match()
        self.assertEqual((inv.match_state, inv.state), ('matched', 'payable'))
        self.assertEqual(inv.received_qty, 40000)

    def test_billing_for_more_than_was_received_is_a_variance_and_can_be_debited(self):
        inv = self._invoice(self._received_po(received=38000), 40000, 10.0)
        inv.action_match()
        self.assertEqual((inv.match_state, inv.state), ('exception', 'draft'))
        self.assertEqual(inv.qty_variance, 2000)
        inv.action_debit_excess()
        note = inv.debit_note_ids
        self.assertEqual(note.amount, 23000.0)  # 2,000 kg x 10 plus 15% VAT
        self.assertEqual(inv.state, 'payable')
        self.assertEqual(inv.payable_amount, 452000.0 - 23000.0)
        self.assertRegex(note.name, r'^DN-\d{4}-\d{5}$')
        with self.assertRaises(UserError):
            note.write({'amount': 1})

    def test_price_above_the_order_is_a_variance(self):
        inv = self._invoice(self._received_po(), 40000, 10.5)
        inv.action_match()
        self.assertEqual(inv.match_state, 'exception')
        self.assertAlmostEqual(inv.price_variance_pct, 5.0)

    def test_variance_can_go_through_the_approval_matrix(self):
        self.env['mos.approval.rule'].create({'doc_type': 'mos.supplier.invoice', 'amount_from': 0, 'group_id': self.env.ref('mos_approvals.group_mos_finance_manager').id})
        inv = self._invoice(self._received_po(), 40000, 10.5)
        inv.action_match()
        inv.action_mos_submit_approval()
        request = inv.mos_approval_request_id
        request.step_ids.filtered(lambda s: s.state == 'pending').with_user(self.fin).action_approve('accepted: market price')
        inv.action_accept_variance()
        self.assertEqual(inv.state, 'payable')

    def test_same_invoice_number_cannot_be_billed_twice(self):
        po = self._received_po()
        self._invoice(po, 40000, 10.0, ref='Inv 001/A')
        with self.assertRaises(Exception):
            self._invoice(po, 1000, 10.0, ref='INV-001A')

    def _payable(self, ref='INV-100', qty=40000):
        inv = self._invoice(self._received_po(qty=qty, received=qty), qty, 10.0, ref=ref)
        inv.action_match()
        return inv

    def test_voucher_shows_gross_vat_withholding_and_net(self):
        inv = self._payable()
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': inv.id})]})
        self.assertEqual((voucher.gross_amount, voucher.vat_amount, voucher.wht_amount, voucher.net_amount), (460000.0, 60000.0, 8000.0, 452000.0))
        voucher.action_submit()
        self.assertEqual(voucher.state, 'approved')  # no band applies: nothing to approve
        self.assertEqual(inv.state, 'vouchered')

    def test_payment_flow_with_partial_payments_and_segregation_of_duties(self):
        inv = self._payable()
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': inv.id})]})
        Payment = self.env['mos.payment']
        with self.assertRaises(UserError):
            Payment.with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 1000, 'account_id': self.account.id})  # not approved yet
        voucher.action_submit()
        with self.assertRaises(UserError):
            Payment.with_user(self.clerk).create({'voucher_id': voucher.id, 'amount': 1000, 'account_id': self.account.id})  # the clerk raised it
        with self.assertRaises(UserError):
            Payment.with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 999999, 'account_id': self.account.id})  # more than owed
        Payment.with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 200000, 'account_id': self.account.id, 'reference': 'TRX1'})
        self.assertEqual(voucher.state, 'partially_paid')
        pay = Payment.with_user(self.cashier).create({'voucher_id': voucher.id, 'amount': 252000, 'account_id': self.account.id})
        self.assertEqual(voucher.state, 'paid')
        self.assertEqual(inv.state, 'paid')
        self.assertRegex(pay.name, r'^PAY-\d{4}-\d{5}$')
        with self.assertRaises(UserError):
            pay.write({'amount': 1})
        with self.assertRaises(UserError):
            pay.unlink()

    def test_cheque_book_numbers_and_gaps(self):
        book = self.env['mos.cheque.book'].create({'account_id': self.account.id, 'prefix': 'CH', 'first_no': 100, 'last_no': 104})
        self.assertEqual(book.unused, 5)
        with self.assertRaises(UserError):
            self.env['mos.cheque.book'].create({'account_id': self.account.id, 'prefix': 'CH', 'first_no': 103, 'last_no': 110})  # overlaps
        book.leaf_ids.filtered(lambda l: l.number == 100).action_void('Spoiled')
        inv = self._payable(ref='INV-200', qty=10000)
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': inv.id})]})
        voucher.action_submit()
        pay = self.env['mos.payment'].with_user(self.cashier).create(
            {'voucher_id': voucher.id, 'amount': voucher.net_amount, 'account_id': self.account.id, 'method': 'cheque', 'cheque_book_id': book.id}
        )
        self.assertEqual(pay.cheque_number, 'CH000101')  # 100 was voided, so the next unused leaf
        self.assertEqual(pay.cheque_leaf_id.state, 'issued')
        with self.assertRaises(UserError):
            pay.cheque_leaf_id.action_void('too late')
        self.assertEqual(book.unused, 3)

    def test_supplier_balance_and_ageing(self):
        inv = self._payable(ref='INV-300', qty=10000)
        self.env.cr.execute('UPDATE mos_supplier_invoice SET invoice_date = CURRENT_DATE - 45 WHERE id = %s', [inv.id])
        inv.invalidate_recordset()
        balance = self.env['mos.supplier.balance'].search([('supplier_id', '=', self.supplier.id)])
        self.assertEqual(len(balance), 1)
        self.assertEqual(balance.balance, inv.payable_amount)
        self.assertEqual(balance.days_31_60, inv.payable_amount)
        self.assertFalse(balance.current)

    # -- the payment screen: several invoices, an amount for each (client screen 25) ---------------------------------------------
    def _two_invoice_voucher(self):
        first = self._invoice(self._received_po(qty=10000, received=10000), 10000, 10.0, ref='INV-A')
        second = self._invoice(self._received_po(qty=5000, received=5000), 5000, 10.0, ref='INV-B')
        (first | second).action_match()
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': first.id}), Command.create({'invoice_id': second.id})]})
        voucher.action_submit()
        return voucher, first, second

    def _pay(self, voucher, **extra):
        return self.env['mos.payment'].with_user(self.cashier).create({'voucher_id': voucher.id, 'account_id': self.account.id, **extra})

    def test_a_payment_without_a_table_pays_the_oldest_invoice_first(self):
        voucher, first, second = self._two_invoice_voucher()
        self.assertEqual((first.payable_amount, second.payable_amount), (113000.0, 56500.0))
        pay = self._pay(voucher, amount=first.payable_amount + 5000)
        by_invoice = {line.invoice_id: line for line in pay.allocation_ids}
        self.assertEqual(by_invoice[first].amount, first.payable_amount)
        self.assertEqual(by_invoice[second].amount, 5000)
        self.assertEqual(by_invoice[second].previously_paid, 0)
        self.assertEqual(by_invoice[second].balance_due, second.payable_amount)
        self.assertEqual(sum(pay.allocation_ids.mapped('amount')), pay.amount)

    def test_a_payment_pays_the_invoices_the_person_chose_by_the_amounts_chosen(self):
        voucher, first, second = self._two_invoice_voucher()
        line_b = voucher.line_ids.filtered(lambda l: l.invoice_id == second)
        line_a = voucher.line_ids.filtered(lambda l: l.invoice_id == first)
        pay = self._pay(
            voucher,
            allocation_ids=[Command.create({'voucher_line_id': line_b.id, 'amount': 20000}), Command.create({'voucher_line_id': line_a.id, 'amount': 0, 'include': False})],
        )
        self.assertEqual(pay.amount, 20000, 'the amount is what the invoices are paid')
        self.assertEqual(pay.allocation_ids.voucher_line_id, line_b, 'a row that pays nothing is not kept')
        self.assertEqual((line_b.paid_amount, line_b.balance, line_a.paid_amount), (20000, second.payable_amount - 20000, 0))
        self.assertEqual(voucher.state, 'partially_paid')
        again = self._pay(voucher, amount=first.payable_amount + 100)
        by_line = {line.voucher_line_id: line for line in again.allocation_ids}
        self.assertEqual(by_line[line_a].amount, first.payable_amount, 'with no table the oldest invoice is paid first')
        self.assertEqual((by_line[line_b].amount, by_line[line_b].previously_paid), (100, 20000), 'the next payment sees what was paid before')
        self.assertEqual(line_b.paid_amount, 20100)

    def test_an_invoice_cannot_be_paid_beyond_its_balance_or_twice_and_the_amount_must_agree(self):
        voucher, first, second = self._two_invoice_voucher()
        line_b = voucher.line_ids.filtered(lambda l: l.invoice_id == second)
        with self.assertRaisesRegex(UserError, 'left to pay'):
            self._pay(voucher, allocation_ids=[Command.create({'voucher_line_id': line_b.id, 'amount': second.payable_amount + 1})])
        with self.assertRaisesRegex(UserError, 'twice'):
            self._pay(voucher, allocation_ids=[Command.create({'voucher_line_id': line_b.id, 'amount': 100}), Command.create({'voucher_line_id': line_b.id, 'amount': 100})])
        with self.assertRaisesRegex(UserError, 'must equal'):
            self._pay(voucher, amount=500, allocation_ids=[Command.create({'voucher_line_id': line_b.id, 'amount': 100})])
        other, *_ = self._two_invoice_voucher_of_other_supplier()
        with self.assertRaisesRegex(UserError, 'on the voucher'):
            self._pay(voucher, allocation_ids=[Command.create({'voucher_line_id': other.line_ids[:1].id, 'amount': 100})])
        self.assertFalse(voucher.payment_ids)

    def _two_invoice_voucher_of_other_supplier(self):
        invoice = self._invoice(self._received_po(qty=1000, received=1000), 1000, 10.0, ref='INV-C')
        invoice.action_match()
        voucher = self.env['mos.payment.voucher'].with_user(self.clerk).create({'supplier_id': self.supplier.id, 'line_ids': [Command.create({'invoice_id': invoice.id})]})
        voucher.action_submit()
        return voucher, invoice

    def test_the_allocation_of_a_payment_never_changes(self):
        voucher, first, second = self._two_invoice_voucher()
        pay = self._pay(voucher, amount=1000)
        with self.assertRaises(UserError):
            pay.allocation_ids.write({'amount': 5})
        with self.assertRaises(UserError):
            pay.allocation_ids.unlink()

    def test_the_payment_screen_fills_the_table_from_the_voucher_and_says_the_amount_in_words(self):
        voucher, first, second = self._two_invoice_voucher()
        form = self.env['mos.payment'].with_user(self.cashier).new({'voucher_id': voucher.id})
        form._onchange_voucher_id()
        self.assertEqual(len(form.allocation_ids), 2)
        self.assertEqual(sum(form.allocation_ids.mapped('amount')), first.payable_amount + second.payable_amount)
        self.assertEqual(form.amount, first.payable_amount + second.payable_amount, 'the amount follows the table')
        self.assertEqual(form.total_balance, form.total_invoice)
        self.assertIn('Hundred', form.amount_in_words)
        row = form.allocation_ids[:1]
        row.include = False
        row._onchange_include()
        self.assertEqual(row.amount, 0)
        self.assertEqual(form.allocation_ids[:1].grn_numbers, first.grn_numbers)
        self.assertRegex(first.grn_numbers, r'/IN/')

    def test_the_payment_shows_the_documents_it_belongs_to(self):
        voucher, first, second = self._two_invoice_voucher()
        pay = self._pay(voucher, amount=voucher.net_amount)
        self.assertEqual(pay.invoice_refs, 'INV-A, INV-B')
        self.assertEqual(set(pay.purchase_refs.split(', ')), {first.purchase_id.name, second.purchase_id.name})
