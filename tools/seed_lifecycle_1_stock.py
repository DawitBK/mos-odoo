"""Lifecycle seed, part 1: the stock side. Reason codes, a second site, lots with expiry and attributes, silo holds, approval delegations,
background reports, stock adjustments in every state, transfers between sites in every state, cycle counts in every state."""

store, admin, finance = user('storekeeper'), user('admin'), user('finance')
inv_manager = in_group('mos_inventory.group_mos_inventory_manager') or admin
unit_kg = env.ref('uom.product_uom_kgm')


def reasons():
    for domain, code, name in (
        ('adjustment', 'ADJ-DMG', 'Damaged in the store'),
        ('adjustment', 'ADJ-FOUND', 'Found when tidying the store'),
        ('count', 'COUNT', 'Count difference'),
        ('scrap', 'SCR-SPOIL', 'Spoiled grain'),
        ('scrap', 'SCR-PEST', 'Pest damage'),
        ('transit_loss', 'TL-SPILL', 'Spillage in transit'),
        ('transit_loss', 'TL-SHORT', 'Short when counted at the other site'),
        ('quality', 'Q-FAIL', 'Failed the specification'),
        ('quality', 'Q-MOIST', 'Moisture above the limit'),
        ('gate_rejection', 'GR-DOCS', 'Documents missing at the gate'),
        ('gate_rejection', 'GR-SAMPLE', 'Sample failed at the gate'),
    ):
        if not env['mos.reason'].search([('domain', '=', domain), ('code', '=', code)]):
            env['mos.reason'].create({'domain': domain, 'code': code, 'name': name})


def second_site():
    if env['mos.site'].search([('code', '=', 'ADAMA')]):
        return
    warehouse = env['stock.warehouse'].create({'name': 'Adama Depot', 'code': 'ADM'})
    env['mos.site'].create({'name': 'Adama Depot', 'code': 'ADAMA', 'warehouse_id': warehouse.id})


def lots():
    stock = site.warehouse_id.lot_stock_id
    plan = (
        ('Flour fortification premix (iron, folic acid)', 'PX', ((-6, 'PX-2025-117', 40), (18, 'PX-2026-031', 120), (75, 'PX-2026-052', 180), (240, 'PX-2026-088', 260))),
        ('Roller mill lubricant', 'LUB', ((29, 'LUB-8812', 60), (400, 'LUB-9031', 120))),
        ('Polypropylene flour bags', 'BAG', ((45, 'BAG-5521', 3000), (330, 'BAG-5598', 6000))),
    )
    made = []
    for name, tag, rows in plan:
        prod = product(name)
        for days_to_expiry, supplier_lot, qty in rows:
            lot = env['stock.lot'].create({'name': '/', 'product_id': prod.id, 'mos_supplier_lot': supplier_lot, 'mos_mfg_date': today - timedelta(days=200), 'mos_expiry_date': today + timedelta(days=days_to_expiry)})
            env['stock.quant'].with_context(inventory_mode=True).create({'product_id': prod.id, 'location_id': stock.id, 'lot_id': lot.id, 'inventory_quantity': qty}).action_apply_inventory()
            for attribute in env['mos.lot.attribute'].search([('category_id', 'parent_of', prod.categ_id.id)]):
                value = {'number': str(round((attribute.min_value + (attribute.max_value or attribute.min_value + 10)) / 2, 1)), 'date': str(today), 'choice': (attribute.choices or 'yes').split(',')[0].strip()}.get(attribute.kind, f'{attribute.name[:12]}-{supplier_lot}')
                env['mos.lot.attribute.value'].create({'lot_id': lot.id, 'attribute_id': attribute.id, 'value': value})
            made.append(lot)
    big = made[2]  # the premix lot with 75 days left is split into a smaller lot for the packing line
    big.action_split(30.0)


def holds():
    inspector = inv_manager
    silos = env['mos.silo'].search([], order='code')
    released = silos[4].code and env['mos.silo.hold'].create({'silo_id': silos[4].id, 'reason': 'cleaning', 'chemical': 'Dry brushing, no chemicals', 'started_at': now - timedelta(days=9), 'planned_end': now - timedelta(days=7)})
    released.with_user(inspector).action_release('Inspected clean, cleared for use')
    env['mos.silo.hold'].create({'silo_id': silos[5].id, 'reason': 'fumigation', 'chemical': 'Phosphine tablets', 'started_at': now - timedelta(days=1), 'planned_end': now + timedelta(days=4)})


def delegations():
    gm, dept = user('gm'), user('deptmanager')
    env['mos.approval.delegation'].with_user(gm).create({'delegator_id': gm.id, 'delegate_id': dept.id, 'date_from': now - timedelta(days=1), 'date_to': now + timedelta(days=6), 'doc_type': 'purchase.order', 'max_amount': 20000})
    env['mos.approval.delegation'].with_user(finance).create({'delegator_id': finance.id, 'delegate_id': gm.id, 'date_from': now - timedelta(days=30), 'date_to': now - timedelta(days=16)})
    env['mos.approval.delegation'].with_user(gm).create({'delegator_id': gm.id, 'delegate_id': finance.id, 'date_from': now + timedelta(days=20), 'date_to': now + timedelta(days=27), 'active': False})


def reports():
    admin_env = env['mos.report.job'].with_user(admin)
    done = [
        admin_env.create({'report': 'stock_movements', 'date_from': today - timedelta(days=30), 'date_to': today}),
        admin_env.create({'report': 'receipt_valuation', 'date_from': today - timedelta(days=90), 'date_to': today}),
        admin_env.create({'report': 'audit_trail', 'date_from': today - timedelta(days=7), 'date_to': today}),
    ]
    for job in done:
        job.action_run()
    admin_env.create({'report': 'stock_movements', 'date_from': today - timedelta(days=365), 'date_to': today})  # waits in the queue for the scheduled job


def adjustments():
    products = []
    for quant in env['stock.quant'].sudo().search([('location_id.usage', '=', 'internal'), ('quantity', '>', 200), ('product_id.is_storable', '=', True), ('lot_id', '=', False)], order='quantity desc'):
        products.append((quant.product_id, quant.location_id, quant.quantity))
    if len(products) < 4:
        raise RuntimeError('not enough stock to adjust')
    reason = {code: env['mos.reason'].search([('code', '=', code)], limit=1) for code in ('ADJ-DMG', 'ADJ-FOUND', 'SCR-SPOIL')}

    def make(index, factor, reason_code, note):
        prod, location, qty = products[index]
        return env['mos.stock.adjustment'].with_user(store).create(
            {'site_id': site.id, 'location_id': location.id, 'reason_id': reason[reason_code].id, 'note': note, 'line_ids': [Command.create({'product_id': prod.id, 'new_qty': round(qty * factor, 1)})]}
        )

    make(0, 0.99, 'ADJ-DMG', 'Bags burst while stacking: counted after cleaning up')  # draft
    waiting = make(1, 0.97, 'SCR-SPOIL', 'Damp patch under the roof leak')
    submit(waiting, store)
    applied = make(2, 1.01, 'ADJ-FOUND', 'Extra pallets found behind the old press')
    submit(applied, store)
    decide(applied)
    applied.with_user(inv_manager).action_apply()
    refused = make(3, 0.9, 'ADJ-DMG', 'Written down after a pest scare')
    submit(refused, store)
    decide(refused, 'reject', 'No evidence of loss on this scale: count again first.')
    cancelled = make(0, 1.02, 'ADJ-FOUND', 'Raised by mistake')
    cancelled.with_user(store).action_cancel()


def transfers():
    mill, depot = site, env['mos.site'].search([('code', '=', 'ADAMA')], limit=1)
    source = mill.warehouse_id.lot_stock_id
    choices = [prod for prod in env['product.product'].search([('is_storable', '=', True)]) if available(prod, source) > 3000]
    if len(choices) < 3:
        raise RuntimeError('not enough available stock to move between sites')
    short_reason = env['mos.reason'].search([('code', '=', 'TL-SHORT')], limit=1)

    def make(prod, qty, note):
        return env['mos.transfer.order'].with_user(store).create({'from_site_id': mill.id, 'to_site_id': depot.id, 'note': note, 'line_ids': [Command.create({'product_id': prod.id, 'qty': qty})]})

    make(choices[0], 500.0, 'Weekly top-up for the depot')  # draft
    transit = make(choices[1], 800.0, 'Bran for the depot customers')
    transit.with_user(store).action_dispatch()
    transit.dispatched_at = now - timedelta(days=5)  # long enough in transit for the late-transfer exception
    full = make(choices[2], 1200.0, 'Opening stock for the depot')
    full.with_user(store).action_dispatch()
    full.with_user(admin).action_receive()
    short = make(choices[0], 1000.0, 'Second delivery to the depot')
    short.with_user(store).action_dispatch()
    short.with_user(admin).action_receive({short.line_ids.id: 985.0}, short_reason)
    recalled = make(choices[1], 300.0, 'Sent by mistake')
    recalled.with_user(store).action_cancel()  # a draft is cancelled
    again = make(choices[2], 250.0, 'Recalled while on the road')
    again.with_user(store).action_dispatch()
    again.with_user(store).action_cancel()


def counts():
    silos = env['mos.silo'].search([], order='code')
    counter = store
    second = inv_manager
    pick = lambda location: env['mos.cycle.count'].with_user(counter).create({'site_id': site.id, 'location_id': location.id})

    in_progress = pick(silos[0].location_id)
    in_progress.action_start()
    in_progress.line_ids[:1].with_user(counter).write({'counted_qty': in_progress.line_ids[:1].system_qty})
    waiting = pick(silos[1].location_id)
    waiting.action_start()
    for line in waiting.line_ids:
        line.with_user(counter).write({'counted_qty': round(line.system_qty * 0.9985, 1)})
    waiting.with_user(counter).action_send_review()
    recounted = pick(silos[2].location_id)
    recounted.action_start()
    for line in recounted.line_ids:
        line.with_user(counter).write({'counted_qty': round(line.system_qty * 0.95, 1)})
    recounted.with_user(counter).action_send_review()  # the 5% difference is above the recount limit: lines go back
    for line in recounted.line_ids.filtered('recount'):
        line.with_user(second).write({'counted_qty': round(line.system_qty * 0.97, 1)})
    recounted.with_user(counter).action_send_review()
    approved = pick(silos[3].location_id)
    approved.action_start()
    for line in approved.line_ids:
        line.with_user(counter).write({'counted_qty': round(line.system_qty * 0.99, 1)})
    approved.with_user(counter).action_send_review()
    approved.with_user(inv_manager).action_review_approve()
    sent_back = pick(silos[4].location_id)
    sent_back.action_start()
    for line in sent_back.line_ids:
        line.with_user(counter).write({'counted_qty': round(line.system_qty * 0.996, 1)})
    sent_back.with_user(counter).action_send_review()
    sent_back.with_user(inv_manager).action_request_changes('The tally is for yesterday: count the new delivery too.', sent_back.line_ids)


step('reasons', reasons)
step('second_site', second_site)
step('lots', lots)
step('silo_holds', holds)
step('delegations', delegations)
step('report_jobs', reports)
step('adjustments', adjustments)
step('transfers', transfers)
step('counts', counts)
