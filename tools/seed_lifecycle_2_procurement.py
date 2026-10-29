"""Lifecycle seed, part 2: procurement from the supplier to the order. Suppliers in every standing, bank changes in every state, invitations,
requests in every state, requests for quotation, quotes, a contract, amendments, supplier returns, receipt reassignments, service receipts,
the supplier portal and the notices owed to suppliers."""

buyer, manager, finance, gm, apclerk, admin = user('buyer'), user('procurement'), user('finance'), user('gm'), user('apclerk'), user('admin')
kg = env.ref('uom.product_uom_kgm')
partner_model = env['res.partner']


def suppliers():
    for partner in partner_model.search([('supplier_rank', '>', 0), ('mos_supplier_state', '=', 'approved')]):
        if not partner.email:
            partner.email = 'orders@' + ''.join(ch for ch in partner.name.lower().split(' ltd')[0] if ch.isalnum()) + '.example'
    newcomers = (
        ('Oromia Seed and Grain Growers', '0012345601', 'approved', '1000555501'),
        ('Kality Packaging Supplies', '0012345602', 'approved', '1000555502'),
        ('Awash Milling Spares', '0012345603', 'candidate', '1000555503'),
        ('Tigray Grain Brokers', '0012345604', 'suspended', None),
        ('Nile Commodities Ltd', '0012345605', 'blocked', None),
    )
    for name, tin, standing, account in newcomers:
        wizard = env['mos.supplier.registration'].with_user(buyer).create(
            {
                'name': name,
                'vat': tin,
                'trade_licence': f'TL/2026/{tin[-4:]}',
                'licence_expires': today + timedelta(days=300),
                'contact': 'Sales office',
                'phone': '+251 11 555 01' + tin[-2:],
                'email': 'sales@' + ''.join(ch for ch in name.lower() if ch.isalnum())[:18] + '.example',
                'address': 'Industrial zone, Addis Ababa',
                'bank_name': 'Commercial Bank',
                'bank_account': account or False,
                'activity': 'Supplies ' + ('wheat and barley' if 'Grain' in name or 'Commodit' in name else 'the mill'),
            }
        )
        wizard.action_register()
        partner = partner_model.search([('vat', '=', tin)], limit=1)
        if standing in ('approved', 'suspended', 'blocked'):
            partner.with_user(manager).action_approve_supplier()
        if standing == 'suspended':
            partner.with_user(manager).action_suspend_supplier('Trade licence under renewal: no new orders until it is shown')
        if standing == 'blocked':
            partner.with_user(gm).action_block_supplier('Delivered short three times and refused to credit it')
    changes = env['mos.supplier.bank.change'].search([('partner_id.vat', 'in', ['0012345601', '0012345602', '0012345603'])])
    by_vat = {c.partner_id.vat: c for c in changes}
    by_vat['0012345602'].with_user(manager).action_verify('Called the finance officer on the number in the signed supply contract')
    by_vat['0012345603'].with_user(manager).action_reject('The account name does not match the trade licence')
    highland = partner_model.search([('name', 'like', 'Highland')], limit=1)
    change = env['mos.supplier.bank.change'].with_user(buyer).create({'partner_id': highland.id, 'new_account': '1000777712345', 'reason': 'Supplier moved to a new bank'})
    change.with_user(manager).action_verify('Called back on the number in the contract: confirmed by the finance officer')
    change.with_user(finance).action_apply()


def invitations():
    invites = env['mos.supplier.invite']
    invites.with_user(buyer).create({'name': 'Gojam Farmers Association', 'email': 'office@gojamfarmers.example'})
    stale = invites.with_user(buyer).create({'name': 'Old Contact Trading', 'email': 'info@oldcontact.example'})
    stale.write({'state': 'expired', 'token': False, 'expires_at': now - timedelta(days=3)})
    used = invites.with_user(buyer).create({'name': 'Sidama Grain Collectors', 'email': 'collect@sidamagrain.example'})
    used.register({'name': 'Sidama Grain Collectors', 'vat': '0012345606', 'email': 'collect@sidamagrain.example', 'phone': '+251 46 555 0106', 'address': 'Hawassa', 'activity': 'Collects wheat from smallholders'})


def requests():
    items = {n: product(n) for n in ('Polypropylene flour bags', 'Roller mill lubricant', 'Plansifter sieve cloth')}
    kality = partner_model.search([('vat', '=', '0012345602')], limit=1)

    def make(reason, name, qty, price, **extra):
        return env['mos.purchase.request'].with_user(buyer).create(
            {'site_id': site.id, 'department': 'Operations', 'reason': reason, 'needed_by': today + timedelta(days=21), 'line_ids': [Command.create({'product_id': items[name].id, 'qty': qty, 'estimated_price': price})], **extra}
        )

    refused = make('Lubricant stock for the whole year', 'Roller mill lubricant', 400, 450.0, priority='low')
    submit(refused, buyer)
    decide(refused, 'reject', 'Order for one quarter first; the price is likely to fall.')
    cancelled = make('Sieve cloth, no longer needed', 'Plansifter sieve cloth', 4, 2600.0)
    cancelled.with_user(buyer).action_cancel()
    approved = env['mos.purchase.request'].search([('state', '=', 'approved'), ('order_id', '=', False)], order='id')
    approved = approved.filtered(lambda r: any(line.qty_to_purchase > 0 for line in r.line_ids))[:1] or approved[:1]
    for line in approved.line_ids.filtered(lambda l: l.qty_to_purchase <= 0):  # stock covers it today: order a safety quantity, with the reason
        line.write({'qty_to_purchase': line.qty * 0.5, 'increase_reason': 'Safety stock for the rainy season'})
    order = approved.with_user(buyer).action_convert(kality)
    confirm_po(order, buyer)


def rfqs():
    plains, lakeside, rift = (partner_model.search([('name', 'like', n)], limit=1) for n in ('Plains', 'Lakeside', 'Rift'))
    wheat = product('Durum wheat (local)')

    def make(invited):
        rfq = env['mos.rfq'].with_user(buyer).create({'site_id': site.id, 'deadline': today + timedelta(days=5), 'invited_ids': [Command.set([p.id for p in invited])], 'line_ids': [Command.create({'product_id': wheat.id, 'qty': 30000})]})
        return rfq

    def offer(rfq, supplier, price, days):
        return env['mos.rfq.quote'].with_user(buyer).create({'rfq_id': rfq.id, 'partner_id': supplier.id, 'lead_days': days, 'valid_until': today + timedelta(days=10), 'line_ids': [Command.create({'rfq_line_id': rfq.line_ids.id, 'unit_price': price})]})

    sent = make([plains, lakeside, rift])
    sent.with_user(buyer).action_send()
    offer(sent, plains, 0.462, 4)
    offer(sent, lakeside, 0.455, 6)
    awarded = make([plains, lakeside, rift])
    awarded.with_user(buyer).action_send()
    offer(awarded, plains, 0.470, 3)
    best = offer(awarded, lakeside, 0.449, 5)
    offer(awarded, rift, 0.481, 2)
    order = awarded.with_user(buyer).action_award(best)
    confirm_po(order, buyer)
    make([plains]).with_user(buyer).action_cancel()
    make([rift, lakeside])  # a draft nobody has sent yet


def contracts():
    existing = env['mos.purchase.contract'].search([], limit=1)
    wheat = product('Bread wheat (local)')
    rift = partner_model.search([('name', 'like', 'Rift')], limit=1)
    active = env['mos.purchase.contract'].with_user(buyer).create({'supplier_id': rift.id, 'product_id': wheat.id, 'qty': 600000, 'price': 61.0, 'valid_from': today - timedelta(days=30), 'valid_to': today + timedelta(days=150), 'quality_note': 'Moisture at most 13.5%, no live insects.'})
    active.with_user(manager).action_activate()
    active.with_user(buyer).action_new_calloff(30000.0)
    closed = env['mos.purchase.contract'].with_user(buyer).create({'supplier_id': rift.id, 'product_id': wheat.id, 'qty': 100000, 'price': 59.0, 'valid_from': today - timedelta(days=200), 'valid_to': today - timedelta(days=20)})
    closed.with_user(manager).action_activate()
    closed.with_user(manager).action_close('Season ended; the full quantity was not called off')
    env['mos.purchase.contract'].with_user(buyer).create({'supplier_id': rift.id, 'product_id': wheat.id, 'qty': 250000, 'price': 62.0, 'valid_from': today + timedelta(days=30), 'valid_to': today + timedelta(days=210)})  # a draft for next season
    if not existing:
        raise RuntimeError('expected the first contract from the base demo')


def quotes():
    orders = env['purchase.order'].search([('state', '=', 'purchase'), ('receipt_status', '!=', 'full'), ('mos_quote_ids', '=', False)], order='id desc', limit=3)
    others = partner_model.search([('supplier_rank', '>', 0), ('mos_supplier_state', '=', 'approved')])
    for order in orders:
        for index, supplier in enumerate((order.partner_id | others[:2] | others[3:4])[:3]):
            env['mos.purchase.quote'].with_user(buyer).create({'order_id': order.id, 'partner_id': supplier.id, 'amount': round(order.amount_total * (1 + 0.025 * (index % 3 if supplier != order.partner_id else 0)), 2), 'valid_until': today + timedelta(days=12), 'note': 'Price held until the date shown'})


def amendments():
    if not env['mos.approval.rule'].search([('doc_type', '=', 'mos.po.amendment'), ('amount_from', '=', 700000)]):
        env['mos.approval.rule'].create({'doc_type': 'mos.po.amendment', 'sequence': 5, 'amount_from': 700000, 'group_id': env.ref('mos_approvals.group_mos_dept_manager').id, 'sla_hours': 24})
    orders = env['purchase.order'].search([('state', '=', 'purchase'), ('receipt_status', '=', 'pending'), ('mos_contract_id', '=', False)], order='id desc', limit=5)
    if len(orders) < 4:
        raise RuntimeError('not enough open orders to amend')

    def make(order, qty_factor, price_factor, reason):
        line = order.order_line[:1]
        return env['mos.po.amendment'].with_user(buyer).create({'purchase_id': order.id, 'reason': reason, 'line_ids': [Command.create({'po_line_id': line.id, 'new_qty': round(line.product_qty * qty_factor, 1), 'new_price': round(line.price_unit * price_factor, 4)})]})

    applied = make(orders[0], 1.05, 1.0, 'The mill needs two more truck loads this month')
    submit(applied, buyer)
    decide(applied)
    applied.with_user(manager).action_apply()
    waiting = make(orders[1], 1.0, 1.03, 'Supplier asked for a higher price after the harvest report')
    submit(waiting, buyer)
    refused = make(orders[2], 0.8, 1.0, 'Plan changed: less wheat needed this month')
    submit(refused, buyer)
    decide(refused, 'reject', 'The supplier has already loaded the full quantity.')
    withdrawn = make(orders[3], 1.1, 1.0, 'Raised in error')
    submit(withdrawn, buyer)
    withdrawn.with_user(buyer).action_cancel()
    make(orders[0], 1.0, 0.99, 'Price agreed by phone, to be confirmed')  # a draft


def returns():
    reason = env['mos.reason'].search([('code', '=', 'Q-FAIL')], limit=1)
    candidates = []
    for invoice in env['mos.supplier.invoice'].search([('state', '=', 'payable')], order='id desc'):
        order = invoice.purchase_id
        line = order.order_line[:1]
        quants = env['stock.quant'].sudo().search([('product_id', '=', line.product_id.id), ('location_id.usage', '=', 'internal'), ('quantity', '>', 6000)], order='quantity desc', limit=1)
        if quants and order.picking_ids and invoice.payable_amount > 420000:
            candidates.append((invoice, order, line.product_id, quants.location_id))
    if len(candidates) < 5:
        raise RuntimeError(f'only {len(candidates)} invoices with returnable stock')

    def make(index, qty, note):
        invoice, order, prod, location = candidates[index]
        received = sum(order.order_line.filtered(lambda l: l.product_id == prod).mapped('qty_received'))
        return invoice, env['mos.supplier.return'].with_user(buyer).create({'purchase_id': order.id, 'product_id': prod.id, 'qty': min(qty, received), 'location_id': location.id, 'reason_id': reason.id, 'note': note})

    make(0, 3000, 'Musty smell noticed after unloading')  # draft
    _, ready = make(1, 2500, 'Stones and sand above the limit')
    ready.with_user(manager).action_approve()
    _, sent = make(2, 4000, 'Insect damage found in the silo')
    sent.with_user(manager).action_approve()
    sent.with_user(buyer).action_dispatch()
    invoice, credited = make(3, 3500, 'Moisture above the limit on the second test')
    credited.with_user(manager).action_approve()
    credited.with_user(buyer).action_dispatch()
    credited.with_user(manager).action_credit(invoice)
    _, dropped = make(4, 2000, 'Raised twice')
    dropped.with_user(buyer).action_cancel()


def reassignments():
    visits = env['mos.gate.visit'].search([('purpose', '=', 'inbound_po'), ('state', '=', 'weighed'), ('grn_state', '=', 'none'), ('purchase_id.partner_id.name', 'like', 'Plains')], order='id desc')
    target = env['purchase.order'].search([('state', '=', 'purchase'), ('receipt_status', '!=', 'full'), ('partner_id.name', 'like', 'Plains')], order='id desc', limit=1)
    if len(visits) < 3 or not target:
        raise RuntimeError('no weighed deliveries from one supplier to move')
    moved, waiting, refused = visits[0], visits[1], visits[2]
    for visit, note in ((moved, 'Booked against last month\'s order by mistake'), (waiting, 'The driver\'s papers name the new order'), (refused, 'Supplier asked for it')):
        env['mos.grn.reassignment'].with_user(buyer).create({'visit_id': visit.id, 'to_order_id': target.id, 'reason': note})
    items = env['mos.grn.reassignment'].search([('visit_id', 'in', [moved.id, refused.id])], order='id')
    env['mos.grn.reassignment'].search([('visit_id', '=', moved.id)]).with_user(manager).action_apply()
    env['mos.grn.reassignment'].search([('visit_id', '=', refused.id)]).with_user(manager).action_reject()
    if not items:
        raise RuntimeError('reassignments were not created')


def services():
    service = env['product.product'].search([('name', '=', 'Plansifter servicing')], limit=1) or env['product.product'].create({'name': 'Plansifter servicing', 'type': 'service', 'purchase_ok': True, 'sale_ok': False, 'uom_id': env.ref('uom.product_uom_unit').id, 'standard_price': 4200.0})
    supplier = partner_model.search([('vat', '=', '0012345601')], limit=1)
    order = env['purchase.order'].with_user(buyer).create({'partner_id': supplier.id, 'order_line': [Command.create({'product_id': service.id, 'product_qty': 3, 'price_unit': 4200.0, 'name': 'Plansifter servicing, per sifter'})]})
    confirm_po(order, buyer)
    line = order.order_line
    env['mos.service.receipt'].with_user(buyer).create({'line_id': line.id, 'qty': 2, 'note': 'Sifters 1 and 2 serviced, sieve frames re-tensioned'})
    env['mos.service.receipt'].with_user(buyer).create({'line_id': line.id, 'qty': 1, 'note': 'Sifter 3 serviced, bearings replaced'})


def visit_suppliers():
    """The earlier demo deliveries have no supplier on the visit although their order has one: fill it in so the lists and the notices show it."""
    for visit in env['mos.gate.visit'].search([('purpose', '=', 'inbound_po'), ('partner_id', '=', False), ('purchase_id', '!=', False)]):
        visit.sudo().with_context(mos_allow_reopen=True).write({'partner_id': visit.purchase_id.partner_id.id})


def portal_and_notices():
    highland = partner_model.search([('name', 'like', 'Highland')], limit=1)
    highland.with_user(manager).mos_portal_invite()
    orders = env['purchase.order'].search([('partner_id', '=', highland.id), ('state', '=', 'purchase')], order='id desc', limit=4)
    invoices = env['mos.supplier.invoice'].search([('supplier_id', '=', highland.id)], order='id desc', limit=1)
    submissions = env['mos.supplier.submission']
    for index, (order, ref, state) in enumerate(zip(orders, ('HF-2026-0412', 'HF-2026-0431', 'HF-2026-0447'), ('reviewed', 'rejected', 'new'))):
        sub = submissions.sudo().create({'supplier_id': highland.id, 'order_id': order.id, 'supplier_ref': ref, 'amount': round(order.amount_total, 2)})
        sub.attach(f'{ref}.pdf', b'%PDF-1.4\n% submitted through the portal\n%%EOF\n')
        if state == 'reviewed':
            sub.with_user(apclerk).action_review(invoices)
        elif state == 'rejected':
            sub.with_user(apclerk).action_reject('The invoice number repeats an earlier invoice: send a corrected one.')
    for line in env['purchase.order.line'].search([('order_id.state', '=', 'purchase'), ('order_id.receipt_status', '!=', 'full'), ('product_id.type', '=', 'consu')], limit=4):
        line.write({'mos_promise_date': today + timedelta(days=4), 'mos_promise_note': 'Truck leaves the farm on Monday'})
    icp.set_str('mos_notify.supplier_notices', 'True')
    notice = env['mos.supplier.notice']
    for visit in env['mos.gate.visit'].search([('purpose', '=', 'inbound_po'), ('partner_id', '!=', False)], order='id desc', limit=6):
        visit._mos_notice_arrival()
        if visit.inspection_id and visit.inspection_id.state == 'decided' and visit.inspection_id.decision != 'reject':
            visit._mos_notice_receipt()
    for pay in env['mos.payment'].search([], limit=3):
        invoices_text = ', '.join(pay.voucher_id.sudo().line_ids.invoice_id.mapped('name'))
        notice.notify(pay.supplier_id, 'payment', pay, f'Payment advice {pay.name}', f'<p>We have paid you {pay.amount:,.2f} for {invoices_text}.</p>')
    rejected = env['mos.lab.inspection'].search([('decision', '=', 'reject')], limit=1)
    if rejected:
        rejected._mos_notice_rejection('The sample failed on moisture and insect damage.')


step('suppliers', suppliers)
step('invitations', invitations)
step('requests', requests)
step('rfqs', rfqs)
step('contracts', contracts)
step('quotes', quotes)
step('amendments', amendments)
step('supplier_returns', returns)
step('visit_suppliers', visit_suppliers)
step('reassignments', reassignments)
step('services', services)
step('portal_and_notices', portal_and_notices)
