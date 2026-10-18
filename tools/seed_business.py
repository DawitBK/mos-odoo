"""The money side of the flour mill demo: supplier invoices, matching, payment vouchers and payments, customers, sales,
deliveries through the gate, customer invoices, receipts and cash sessions. Runs after seed_demo.py, in the same Odoo shell.
Every document goes through the real checks (matching tolerances, the approval matrix, segregation of duties); dates are
then spread over the last month so ageing and trends look real.
"""

import random
from datetime import timedelta

from odoo import Command, fields

random.seed(20261019)

site = env['mos.site'].search([('code', '=', 'MILL')], limit=1)
U = lambda login: env['res.users'].search([('login', '=', f'{login}@examplecorp.com')], limit=1)
guard, verifier = U('mos.guard'), U('mos.verifier')
deptmgr, finance, gm = U('mos.deptmanager'), U('mos.finance'), U('mos.gm')
apclerk, cashier, salesrep = U('mos.apclerk'), U('mos.cashier'), U('mos.salesrep')
G = lambda x: env.ref(x).id
APPROVER_BY_GROUP = {G('mos_approvals.group_mos_dept_manager'): deptmgr, G('mos_approvals.group_mos_finance_manager'): finance, G('mos_approvals.group_mos_general_manager'): gm}
today = site.business_date()
now = fields.Datetime.now()


def approve_all(doc):
    """Decide every pending stage of the document's request with the right approver."""
    while True:
        step = env['mos.approval.step'].sudo().search([('request_id', '=', doc.mos_approval_request_id.id), ('state', '=', 'pending')], limit=1)
        if not step:
            return
        step.with_user(APPROVER_BY_GROUP[step.group_id.id]).action_approve('Approved')


# --- paying accounts and a cheque book -----------------------------------------------------------------------------
bank = env['mos.paying.account'].create({'name': 'Main operating account', 'kind': 'bank', 'site_id': site.id})
env['mos.paying.account'].create({'name': 'Petty cash', 'kind': 'cash', 'site_id': site.id})
wallet = env['mos.paying.account'].create({'name': 'Mobile-money wallet', 'kind': 'wallet', 'site_id': site.id})
book = env['mos.cheque.book'].create({'account_id': bank.id, 'prefix': 'CH', 'first_no': 4001, 'last_no': 4080})
book.leaf_ids.filtered(lambda l: l.number == 4003).action_void('Spoiled while printing')

# --- supplier invoices for the wheat that arrived -----------------------------------------------------------------
visits = env['mos.gate.visit'].search([('state', '=', 'exited'), ('grn_state', '=', 'confirmed')], order='exited_at')
invoices = env['mos.supplier.invoice']
for idx, visit in enumerate(visits):
    insp = visit.inspection_id
    po = visit.purchase_id
    price = po.order_line[:1].price_unit
    qty = insp.payable_qty
    billed_qty = qty + (700 if idx % 11 == 5 else 0)  # a few suppliers bill for more than was weighed in
    billed_price = round(price * (1.04 if idx % 13 == 7 else 1.0), 4)
    inv = env['mos.supplier.invoice'].with_user(apclerk).create(
        {
            'supplier_id': visit.partner_id.id,
            'supplier_ref': f'{visit.partner_id.name[:3].upper()}-{random.randint(1000, 9999)}-{idx}',
            'invoice_date': (visit.exited_at or now).date() + timedelta(days=1),
            'purchase_id': po.id,
            'product_id': insp.product_id.id,
            'qty': billed_qty,
            'unit_price': billed_price,
        }
    )
    inv.action_match()
    if inv.match_state == 'exception':
        if billed_price > price:  # a price rise: finance decides whether to accept it
            inv.with_user(apclerk).action_mos_submit_approval()
            approve_all(inv)
            inv.with_user(apclerk).action_accept_variance()
        else:  # billed more than received: the excess goes back as a debit note
            inv.with_user(apclerk).action_debit_excess()
    invoices |= inv

# --- vouchers and payments --------------------------------------------------------------------------------------------
payable = invoices.filtered(lambda i: i.state == 'payable').sorted('invoice_date')
cut = int(len(payable) * 0.65)
by_supplier = {}
for inv in payable[:cut]:
    by_supplier.setdefault(inv.supplier_id, env['mos.supplier.invoice'])
    by_supplier[inv.supplier_id] |= inv
vouchers = env['mos.payment.voucher']
for supplier, invs in by_supplier.items():
    chunks = [invs[i:i + 3] for i in range(0, len(invs), 3)]
    for chunk in chunks:
        # Matching already drafted a voucher per invoice (the receipt started the payment); the clerk groups them instead.
        for auto in env['mos.payment.voucher.line'].search([('invoice_id', 'in', chunk.ids), ('live', '=', True)]).voucher_id:
            auto.action_cancel()
        voucher = env['mos.payment.voucher'].with_user(apclerk).create({'supplier_id': supplier.id, 'line_ids': [Command.create({'invoice_id': i.id}) for i in chunk]})
        voucher.action_submit()
        if voucher.state == 'submitted':
            approve_all(voucher)
        vouchers |= voucher
for n, voucher in enumerate(vouchers.sorted('id')):
    voucher = voucher.with_user(cashier)
    if voucher.state not in ('approved', 'partially_paid'):
        continue
    method = ('transfer', 'cheque', 'transfer', 'wallet')[n % 4]
    account = wallet if method == 'wallet' else bank
    vals = {'voucher_id': voucher.id, 'account_id': account.id, 'method': method, 'amount': voucher.remaining_amount}
    if method == 'cheque':
        vals['cheque_book_id'] = book.id
    if method != 'cheque':
        vals['reference'] = f'TRX{random.randint(100000, 999999)}'
    if n % 5 == 3:  # a part payment
        vals['amount'] = round(voucher.remaining_amount * 0.6, 2)
    if n < len(vouchers) - 4:  # the newest few are still waiting to be paid
        env['mos.payment'].with_user(cashier).create(vals)

# Spread the supplier-side dates over the month.
for visit in visits:
    stamp = (visit.exited_at or now).date()
    env.cr.execute('UPDATE mos_supplier_invoice SET invoice_date = %s WHERE purchase_id = %s', [stamp + timedelta(days=1), visit.purchase_id.id])
    env.cr.execute('UPDATE mos_payment_voucher SET date = %s WHERE id IN (SELECT voucher_id FROM mos_payment_voucher_line l JOIN mos_supplier_invoice i ON i.id = l.invoice_id WHERE i.purchase_id = %s)', [stamp + timedelta(days=3), visit.purchase_id.id])
env.cr.execute("UPDATE mos_payment p SET date = v.date + 2 FROM mos_payment_voucher v WHERE v.id = p.voucher_id")

# --- customers and sales ---------------------------------------------------------------------------------------------
CUSTOMERS = [
    ('Golden Crust Bakeries', 3_000_000),
    ('Addis Pasta and Noodles', 2_000_000),
    ('Sunrise Retail Cooperative', 800_000),
    ('Harvest Biscuit Co.', 1_500_000),
    ('Corner Shops Union', 120_000),
    ('Mercato Wholesale', 2_500_000),
]
customers = [env['res.partner'].create({'name': n, 'is_company': True, 'customer_rank': 1, 'mos_credit_limit': lim, 'mos_credit_mode': 'block'}) for n, lim in CUSTOMERS]
flour = {p.name: p for p in env['product.product'].search([('categ_id.name', '=', 'Flour')])}
bran = env['product.product'].search([('name', '=', 'Wheat bran')], limit=1)
catalogue = [(flour['Bread flour 72%'], 0.84, 5), (flour['Semolina'], 0.98, 3), (flour['Whole-wheat flour'], 0.88, 2), (bran, 0.24, 2)]
warehouse = site.warehouse_id
plates = ['AA-', 'OR-', 'AM-', 'TG-']
delivered = env['mos.delivery.order']
for i in range(30):
    cust = random.choice(customers)
    product, base_price, _w = random.choices(catalogue, weights=[w for *_x, w in catalogue])[0]
    qty = random.choice([8000, 10000, 12000, 15000, 20000, 25000, 30000])
    price = round(base_price * random.uniform(0.97, 1.05), 3)
    order = env['sale.order'].with_user(salesrep).create(
        {'partner_id': cust.id, 'warehouse_id': warehouse.id, 'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': qty, 'price_unit': price})]}
    )
    if order._mos_approval_required():
        order.action_mos_submit_approval()
        approve_all(order)
    order.with_user(salesrep).action_confirm()
    delivery = env['mos.delivery.order'].with_user(salesrep).create({'sale_id': order.id, 'site_id': site.id, 'plate': f'{random.choice(plates)}{random.randint(10000, 99999)}', 'driver_name': random.choice(['Abel', 'Chaltu', 'Dawit', 'Eden', 'Fitsum'])})
    delivery.action_release()
    visit = delivery.visit_id.with_user(guard)
    visit.action_arrive()
    visit.action_start_weighing()
    tare = float(random.randrange(14000, 17500, 10))
    loaded = round(tare + qty * random.uniform(0.996, 1.01), 1)
    visit.ticket_id.record_first(tare)
    visit.ticket_id.record_second(loaded)
    visit.ticket_id.with_user(verifier).verify_blind(tare, loaded)
    delivery.with_user(salesrep).action_dispatch()
    visit.action_clear_exit()
    visit.action_exit()
    delivered |= delivery
    # Backdate: spread the deliveries over the last four weeks, oldest first.
    days = int(27 - i * 0.9)
    stamp = now - timedelta(days=max(days, 0), hours=random.randint(0, 8))
    env.cr.execute('UPDATE mos_gate_visit SET arrived_at = %s, exited_at = %s WHERE id = %s', [stamp - timedelta(hours=2), stamp, visit.id])
    env.cr.execute('UPDATE mos_customer_invoice SET date = %s WHERE id = %s', [stamp.date(), delivery.invoice_id.id])
    delivery.invoice_id.write({'fiscal_receipt_ref': f'FS-{100000 + i}'})

# --- receipts, advances and cash sessions -----------------------------------------------------------------------------
env['mos.customer.invoice'].invalidate_model()
open_invoices = env['mos.customer.invoice'].search([], order='date')
Receipt = env['mos.receipt']
session = env['mos.cash.session'].with_user(cashier).create({'float_amount': 5000})
paid = 0
for n, inv in enumerate(open_invoices):
    r = n % 7
    if r in (5, 6):  # these customers have not paid yet: ageing and credit exposure stay interesting
        continue
    method = ('transfer', 'cheque', 'cash', 'wallet', 'transfer')[n % 5]
    amount = inv.balance if r != 3 else round(inv.balance * 0.5, 2)
    if method == 'cash' and amount > 60000:
        method = 'transfer'
    Receipt.with_user(cashier).create({'customer_id': inv.customer_id.id, 'invoice_id': inv.id, 'amount': amount, 'method': method, 'reference': f'SLIP-{1000 + n}' if method != 'cash' else False})
    paid += 1
# A customer pays ahead of delivery.
Receipt.with_user(cashier).create({'customer_id': customers[0].id, 'amount': 12000, 'method': 'transfer', 'reference': 'ADVANCE-GCB-17'})
session.with_user(cashier).action_close(session.expected_cash - 150, 'Change given to a customer was not recorded')
env['mos.cash.session'].with_user(cashier).create({'float_amount': 5000})  # today's till is open
env.cr.execute("UPDATE mos_receipt r SET date = i.date + 3 FROM mos_customer_invoice i WHERE i.id = r.invoice_id")

env.cr.commit()
print(
    'MOS business data ready:',
    env['mos.supplier.invoice'].search_count([]), 'supplier invoices,',
    env['mos.payment.voucher'].search_count([]), 'vouchers,',
    env['mos.payment'].search_count([]), 'payments,',
    env['sale.order'].search_count([]), 'sales orders,',
    env['mos.customer.invoice'].search_count([]), 'customer invoices,',
    env['mos.receipt'].search_count([]), 'receipts.',
)
