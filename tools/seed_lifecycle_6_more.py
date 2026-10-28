"""Lifecycle seed, part 6: what the first five parts left thin. Lot attributes with values, finished-goods inspections and packing from the
batches they release, stock status changes, payment runs, journal exports, guard shifts."""

analyst, labmgr, production, finance, cashier, guard, gatesup, apclerk = user('analyst'), user('labmanager'), user('production'), user('finance'), user('cashier'), user('guard'), user('gatesup'), user('apclerk')
inv_manager = in_group('mos_inventory.group_mos_inventory_manager')
store = user('storekeeper')


def lot_attributes():
    materials = env['product.category'].search([('name', '=', 'Materials and packaging')], limit=1) or env['product.category'].create({'name': 'Materials and packaging'})
    lots = env['stock.lot'].search([('mos_expiry_date', '!=', False)])
    for prod in lots.product_id.filtered(lambda p: not p.categ_id):
        prod.categ_id = materials
    for lot in lots:
        category = lot.product_id.categ_id
        if not env['mos.lot.attribute'].search([('category_id', '=', category.id)]):
            env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Supplier certificate no.', 'kind': 'text', 'required': False, 'sequence': 10})
            env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Storage', 'kind': 'choice', 'choices': 'Dry store,Cold store,Open yard', 'sequence': 20})
            env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Moisture of the packing (%)', 'kind': 'number', 'min_value': 0.0, 'max_value': 15.0, 'sequence': 30})
    for lot in env['stock.lot'].search([('mos_expiry_date', '!=', False)]):
        for attribute in env['mos.lot.attribute'].search([('category_id', 'parent_of', lot.product_id.categ_id.id)]):
            if attribute in lot.mos_attribute_ids.attribute_id:
                continue
            value = {'number': '9.5', 'date': str(today), 'choice': (attribute.choices or 'Dry store').split(',')[0].strip()}.get(attribute.kind, f'CERT-{lot.name[-5:]}')
            env['mos.lot.attribute.value'].create({'lot_id': lot.id, 'attribute_id': attribute.id, 'value': value})
    if not env['mos.lot.attribute.value'].search_count([]):
        raise RuntimeError('no lot attribute value was recorded')


def finished_goods():
    flour_recipe = env['mos.recipe'].search([('state', '=', 'approved')], limit=1)
    flour, bran = flour_recipe.product_id, product('Wheat bran')
    premix = flour.product_tmpl_id.mos_premix_id
    centre = env['mos.work.centre'].search([('kind', '=', 'milling')], limit=1)
    silos = env['mos.silo'].search([('product_id.name', 'like', 'Bread')], order='id')
    silo = next((s for s in silos if s.fresh().level_kg > 60000 and not env['mos.silo.hold'].is_held(s)), None)
    if not silo:
        raise RuntimeError('no silo with enough wheat')
    batches = env['mos.batch']
    for number, qty in enumerate((24000, 20000, 16000)):
        run = env['mos.milling.run'].with_user(production).create(
            {
                'site_id': site.id,
                'work_centre_id': centre.id,
                'run_date': today - timedelta(days=3 - number),
                'shift': ('day', 'night', 'day')[number],
                'input_ids': [Command.create({'silo_id': silo.id, 'product_id': silo.product_id.id, 'qty_kg': qty})],
                'output_ids': [Command.create({'product_id': flour.id, 'kind': 'flour', 'qty_kg': round(qty * 0.74)}), Command.create({'product_id': bran.id, 'kind': 'bran', 'qty_kg': round(qty * 0.2)})],
                'dosing_ids': [Command.create({'premix_id': premix.id, 'actual_kg': round(qty * 0.74 / 1000.0 * flour.product_tmpl_id.mos_premix_g_per_t / 1000.0, 2)})] if premix else [],
            }
        )
        run.with_user(production).action_complete()
        batches |= run.batch_ids.filtered(lambda b: b.kind == 'flour')
    released, rejected, waiting = batches[:3]
    for batch, values, outcome in ((released, (0.58, 12.6, 30.5), 'release'), (rejected, (0.95, 15.8, 22.0), 'reject')):
        insp = env['mos.batch.inspection'].with_user(analyst).create({'batch_id': batch.id})
        for line, value in zip(insp.spec_id.line_ids, values):
            insp.record_result(line, value)
        insp.action_submit()
        insp.with_user(labmgr).decide(outcome, 'Meets the specification' if outcome == 'release' else 'Ash and moisture above the limits: not for sale')
    waiting_insp = env['mos.batch.inspection'].with_user(analyst).create({'batch_id': waiting.id})
    for line, value in zip(waiting_insp.spec_id.line_ids, (0.61, 12.9, 29.8)):
        waiting_insp.record_result(line, value)
    waiting_insp.action_submit()  # tested, waiting for the lab manager
    bag50 = product('Bread flour 50 kg bag')
    bag25 = product('Bread flour 25 kg bag')
    stock = site.warehouse_id.lot_stock_id
    for bags in (product('Polypropylene flour bags'),):
        env['stock.quant'].with_context(inventory_mode=True).create({'product_id': bags.id, 'location_id': stock.id, 'inventory_quantity': env['stock.quant']._get_available_quantity(bags, stock) + 2000}).action_apply_inventory()
    for bag in (bag50, bag25):
        env['stock.quant'].with_context(inventory_mode=True).create({'product_id': bag.id, 'location_id': stock.id, 'inventory_quantity': env['stock.quant']._get_available_quantity(bag, stock) + 600}).action_apply_inventory()
    done = env['mos.pack.run'].with_user(production).create(
        {'site_id': site.id, 'work_centre_id': env['mos.work.centre'].search([('kind', '=', 'packing')], limit=1).id, 'source_batch_id': released.id, 'spillage_kg': 18.0, 'line_ids': [Command.create({'product_id': bag50.id, 'bags': 200}), Command.create({'product_id': bag25.id, 'bags': 240})]}
    )
    done.with_user(production).action_complete()
    env['mos.pack.run'].with_user(production).create({'site_id': site.id, 'work_centre_id': done.work_centre_id.id, 'source_batch_id': released.id, 'spillage_kg': 0.0, 'line_ids': [Command.create({'product_id': bag50.id, 'bags': 80})]})  # planned for tomorrow


def status_changes():
    quants = env['stock.quant'].sudo().search([('location_id.usage', '=', 'internal'), ('location_id.mos_stock_status', '=', 'available'), ('lot_id', '=', False), ('quantity', '>', 3000), ('product_id.is_storable', '=', True)], order='quantity desc', limit=4)
    if len(quants) < 4:
        raise RuntimeError('not enough stock to hold')
    reason = env['mos.reason'].search([('domain', '=', 'status_change')], limit=1)
    Change = env['mos.stock.status.change']
    made = []
    for quant, qty, note in zip(quants, (1500.0, 800.0, 2000.0, 600.0), ('Insect activity found in the corner of the bin', 'Customer complaint: sample under test', 'Moisture meter reading high', 'Mislabelled pallet')):
        made.append(Change.with_user(store).create({'site_id': site.id, 'product_id': quant.product_id.id, 'qty': qty, 'location_id': quant.location_id.id, 'from_status': 'available', 'to_status': 'quarantine', 'reason_id': reason.id, 'note': note}))
    made[1].with_user(inv_manager).action_approve()
    made[2].with_user(inv_manager).action_approve()
    made[2].with_user(store).action_apply()  # moved into quarantine
    made[3].with_user(inv_manager).action_reject()


def payment_runs():
    account = env['mos.paying.account'].search([('kind', '=', 'bank')], limit=1)
    Run = env['mos.payment.run'].with_user(cashier)
    # the two oldest approved vouchers are paid in a run; the rest wait
    executed = Run.create({'account_id': account.id, 'method': 'transfer'})
    executed.action_propose()
    payable = executed.line_ids.filtered(lambda l: not l.voucher_id.approved_bank_digest or l.voucher_id.approved_bank_digest == l.voucher_id._mos_bank_digest())  # a voucher whose account changed after its approval must be raised again
    for line in executed.line_ids - payable[:2]:
        line.include = False
    for voucher in executed.line_ids.filtered('include').voucher_id.filtered(lambda v: not v.mos_attachment_ids.filtered(lambda a: a.kind == 'deposit_slip')):  # the deposit slip rule is on: a transfer needs its slip attached first
        env['mos.attachment'].with_user(apclerk).create({'name': f'deposit-slip-{voucher.name}.pdf', 'file': pdf(f'slip {voucher.name}'), 'res_model': voucher._name, 'res_id': voucher.id, 'kind': 'deposit_slip', 'note': 'Bank deposit slip'})
    executed.action_execute()
    waiting = Run.create({'account_id': account.id, 'method': 'transfer'})
    try:
        waiting.action_propose()
    except Exception:  # nothing is left to pay: the second run stays an empty draft, which the screen still shows
        pass
    Run.create({'account_id': account.id, 'method': 'transfer'}).action_cancel()


def journal_exports():
    Batch = env['mos.journal.batch'].with_user(finance)
    exported = 0
    for start, end in ((today - timedelta(days=60), today - timedelta(days=31)), (today - timedelta(days=30), today)):
        try:
            Batch.export(start, end)
            exported += 1
        except Exception as error:
            print(f'      export {start}..{end}: {str(error)[:150]}')
    if not exported:
        raise RuntimeError('no export could be made')


def shifts():
    for who, days_ago in ((gatesup, 4), (gatesup, 3), (gatesup, 2)):
        shift = env['mos.gate.shift'].with_user(who).create({'site_id': site.id, 'guard_id': who.id})
        shift.sudo().write({'handover_notes': 'All trucks released; seal log checked.'})
        shift.with_user(who).action_close()
        stamp = now - timedelta(days=days_ago, hours=10)
        shift.sudo().write({'started_at': stamp, 'ended_at': stamp + timedelta(hours=8)})


step('lot_attributes', lot_attributes)
step('finished_goods', finished_goods)
step('status_changes', status_changes)
step('payment_runs', payment_runs)
step('journal_exports', journal_exports)
step('shifts', shifts)
