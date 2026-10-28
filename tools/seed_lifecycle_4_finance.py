"""Lifecycle seed, part 4: sales and money. Quotations, orders waiting for approval or over the credit limit, confirmed orders not yet
delivered (the demand the planner works from), deliveries waiting at the gate and on the scale, a new customer, returns, vouchers
waiting for approval or refused, supplier invoices still being entered, a payment reversal, planning suggestions."""

salesrep, salesmgr, guard, verifier, finance, apclerk, production = user('salesrep'), user('salesmgr'), user('guard'), user('verifier'), user('finance'), user('apclerk'), user('production')
store, labmgr = user('storekeeper'), user('labmanager')
Partner = env['res.partner']


def _customer(name):
    found = Partner.search([('name', 'like', name), ('customer_rank', '>', 0)], limit=1)
    if not found:
        raise RuntimeError(f'customer {name!r} is missing')
    return found


def _order(customer, prod, qty, price, who=None):
    return env['sale.order'].with_user(who or salesrep).create(
        {'partner_id': customer.id, 'warehouse_id': site.warehouse_id.id, 'order_line': [Command.create({'product_id': prod.id, 'product_uom_qty': qty, 'price_unit': price})]}
    )


def sales():
    bread, semolina, whole = product('Bread flour 72%'), product('Semolina'), product('Whole-wheat flour')
    golden, addis, corner, mercato, harvest = (_customer(n) for n in ('Golden Crust', 'Addis Pasta', 'Corner Shops', 'Mercato', 'Harvest'))

    _order(golden, bread, 12000, 0.85)  # a quotation nobody has submitted
    waiting = _order(corner, bread, 200000, 0.84)  # over this customer's credit limit: the general manager decides
    submit(waiting, salesrep, 'action_mos_submit_approval')
    refused = _order(harvest, semolina, 15000, 0.99)
    submit(refused, salesrep, 'action_mos_submit_approval')
    decide(refused, 'reject', 'The customer still has two overdue invoices.')
    dropped = _order(mercato, whole, 9000, 0.88)
    dropped.with_user(salesrep).action_cancel()
    # confirmed and waiting to be delivered: the demand the planner works from
    for customer, prod, qty, price in ((addis, semolina, 40000, 0.98), (mercato, whole, 50000, 0.89), (golden, bread, 60000, 0.85)):
        order = _order(customer, prod, qty, price)
        if order._mos_approval_required():
            submit(order, salesrep, 'action_mos_submit_approval')
            decide(order)
        order.with_user(salesrep).action_confirm()
    # a truck expected at the gate, and one on the scale
    stock_flour = [p for p in (bread, whole, semolina) if available(p, site.warehouse_id.lot_stock_id) > 6000]
    if not stock_flour:
        raise RuntimeError('no flour in stock to load a truck')
    for number, (customer, plate, driver) in enumerate(((golden, 'AA-48213', 'Fitsum'), (mercato, 'OR-30944', 'Abel'))):
        prod = stock_flour[number % len(stock_flour)]
        order = _order(customer, prod, 5000, prod.list_price or 0.85)
        if order._mos_approval_required():
            submit(order, salesrep, 'action_mos_submit_approval')
            decide(order)
        order.with_user(salesrep).action_confirm()
        delivery = env['mos.delivery.order'].with_user(salesrep).create({'sale_id': order.id, 'site_id': site.id, 'plate': plate, 'driver_name': driver})
        delivery.action_release()
        if number == 1:
            visit = delivery.visit_id.with_user(guard)
            visit.action_arrive()
            visit.action_start_weighing()
            visit.ticket_id.record_first(15240.0)
    Partner.create({'name': 'Bole Hotels Group', 'is_company': True, 'customer_rank': 1, 'mos_credit_limit': 250000.0, 'mos_credit_mode': 'block', 'email': 'purchasing@bolehotels.example'})


def customer_returns():
    reason = env['mos.reason'].search([('domain', '=', 'customer_return')], limit=1)
    delivered = env['mos.delivery.order'].search([('state', '=', 'dispatched')], order='id desc', limit=8)
    made = []
    for delivery in delivered:
        prod = delivery.invoice_id.product_id
        if prod and not env['mos.customer.return'].search_count([('delivery_id', '=', delivery.id)]):
            made.append((delivery, prod))
        if len(made) == 3:
            break
    if len(made) < 3:
        raise RuntimeError('not enough deliveries to return from')
    Return = env['mos.customer.return']
    announced = Return.with_user(salesrep).create({'site_id': site.id, 'customer_id': made[0][0].partner_id.id, 'delivery_id': made[0][0].id, 'product_id': made[0][1].id, 'qty': 250.0, 'reason_id': reason.id, 'note': 'Customer reports wet bags on arrival'})
    scrapped = Return.with_user(salesrep).create({'site_id': site.id, 'customer_id': made[1][0].partner_id.id, 'delivery_id': made[1][0].id, 'product_id': made[1][1].id, 'qty': 400.0, 'reason_id': reason.id, 'note': 'Bags crushed under another load'})
    scrapped.with_user(store).action_receive()
    scrapped.with_user(labmgr).action_decide('scrap', 'Wet flour, mould visible: not fit for sale')
    cancelled = Return.with_user(salesrep).create({'site_id': site.id, 'customer_id': made[2][0].partner_id.id, 'delivery_id': made[2][0].id, 'product_id': made[2][1].id, 'qty': 100.0, 'reason_id': reason.id, 'note': 'Customer changed their mind'})
    cancelled.with_user(salesrep).action_cancel()
    if announced.state != 'draft':
        raise RuntimeError('the announced return changed state')


def vouchers():
    payable = env['mos.supplier.invoice'].search([('state', '=', 'payable')], order='id desc')
    chosen = []
    for supplier in payable.supplier_id:
        mine = payable.filtered(lambda i: i.supplier_id == supplier)
        if mine:
            chosen.append(mine[:1])
        if len(chosen) == 2:
            break
    if len(chosen) < 2:
        raise RuntimeError('need payable invoices from two suppliers')
    outcomes = []
    for invoice in chosen:
        for auto in env['mos.payment.voucher.line'].search([('invoice_id', 'in', invoice.ids), ('live', '=', True)]).voucher_id:
            auto.action_cancel()
        voucher = env['mos.payment.voucher'].with_user(apclerk).create({'supplier_id': invoice.supplier_id.id, 'line_ids': [Command.create({'invoice_id': invoice.id})]})
        submit(voucher, apclerk)
        outcomes.append(voucher)
    decide(outcomes[1], 'reject', 'The bank account on the invoice is not the one on file.')


def invoices():
    orders = env['purchase.order'].search([('state', '=', 'purchase'), ('receipt_status', '=', 'full')], order='id desc')
    billed = env['mos.supplier.invoice'].search([]).purchase_id
    free = (orders - billed)[:2]
    if len(free) < 2:
        raise RuntimeError('every received order already has an invoice')
    for index, order in enumerate(free):
        line = order.order_line[:1]
        invoice = env['mos.supplier.invoice'].with_user(apclerk).create(
            {
                'supplier_id': order.partner_id.id,
                'supplier_ref': f'{order.partner_id.name[:3].upper()}-{7700 + index}',
                'invoice_date': today,
                'purchase_id': order.id,
                'product_id': line.product_id.id,
                'qty': line.qty_received,
                'unit_price': line.price_unit,
            }
        )
        if index == 1:
            invoice.with_user(apclerk).action_cancel()


def reversal():
    payment = env['mos.payment'].search([('method', '=', 'transfer')], order='id desc', limit=1)
    env['mos.payment.reversal'].with_user(finance).create({'payment_id': payment.id, 'reason': 'The transfer went to the wrong account: to be paid again'})


def planning():
    tender = _order(_customer('Mercato'), product('Whole-wheat flour'), 600000, 0.89)  # more than the stock covers: the planner has work to do
    if tender._mos_approval_required():
        submit(tender, salesrep, 'action_mos_submit_approval')
        decide(tender)
    tender.with_user(salesrep).action_confirm()
    Suggestion = env['mos.mrp.suggestion'].with_user(production)
    Suggestion.generate()
    open_ones = Suggestion.search([('state', '=', 'open')], order='id')
    if not open_ones:
        raise RuntimeError('the planner found no shortfall')
    open_ones[0].action_accept()
    Suggestion.generate()  # what the accepted suggestion covers is no longer short; anything left over is dismissed below
    left = Suggestion.search([('state', '=', 'open')], order='id')
    if left:
        left[0].action_dismiss()


step('sales', sales)
step('customer_returns', customer_returns)
step('vouchers', vouchers)
step('invoices', invoices)
step('payment_reversal', reversal)
step('planning', planning)
