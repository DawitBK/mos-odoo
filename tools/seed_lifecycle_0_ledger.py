"""Lifecycle seed, part 0: the stock ledger.

The first demo seed put its opening stock into the silos without a stock movement, so every bin card and stock card showed a ledger
that did not reach the stock on hand (a closing balance far below zero). This part gives each location whose ledger differs from its
stock a real inventory adjustment for the difference, dated before the first movement so the running balance never dips below zero.
It acts only where there is a difference, so running it again (or on a database built with the corrected seed) changes nothing.
It also gives the first warehouse the name a person would use ("Main Warehouse") instead of the database's default.
"""

from datetime import timedelta

Quant = env['stock.quant'].sudo()
MoveLine = env['stock.move.line'].sudo()


def ledger_balance(prod, location):
    domain = [('state', '=', 'done'), ('product_id', '=', prod.id)]
    received = MoveLine.search(domain + [('location_dest_id', '=', location.id)])
    sent = MoveLine.search(domain + [('location_id', '=', location.id)])
    return sum(received.mapped('quantity_product_uom')) - sum(sent.mapped('quantity_product_uom'))


def warehouse_name():
    warehouse = env['stock.warehouse'].search([('code', '=', 'WH')], limit=1)
    if warehouse and warehouse.name != 'Main Warehouse':
        warehouse.name = 'Main Warehouse'


def reconcile_ledger():
    fixed = 0
    for location in env['stock.location'].search([('usage', '=', 'internal')]):
        rows = Quant.search([('location_id', '=', location.id)])
        for prod in rows.product_id.filtered('is_storable'):
            quants = rows.filtered(lambda q: q.product_id == prod)
            on_hand = sum(quants.mapped('quantity'))
            gap = round(on_hand - ledger_balance(prod, location), 3)
            if abs(gap) < 0.001:
                continue
            row = max(quants, key=lambda q: q.quantity)
            lot = row.lot_id
            first = MoveLine.search([('state', '=', 'done'), ('product_id', '=', prod.id), '|', ('location_id', '=', location.id), ('location_dest_id', '=', location.id)], order='date', limit=1)
            when = first.date - timedelta(days=1) if first and gap > 0 else env.cr.now()
            # Bring the stock down to what the ledger says, then post the difference as the adjustment it really is.
            Quant._update_available_quantity(prod, location, -gap, lot_id=lot or None)
            target = Quant.search([('product_id', '=', prod.id), ('location_id', '=', location.id), ('lot_id', '=', lot.id)], limit=1)
            Quant.with_context(inventory_mode=True).create(
                {'product_id': prod.id, 'location_id': location.id, 'lot_id': lot.id, 'inventory_quantity': (target.quantity if target else 0.0) + gap}
            ).action_apply_inventory()
            lines = MoveLine.search([('product_id', '=', prod.id), '|', ('location_id', '=', location.id), ('location_dest_id', '=', location.id), ('move_id.is_inventory', '=', True)], order='id desc', limit=1)
            lines.write({'date': when})
            lines.move_id.write({'date': when})
            fixed += 1
    print(f'ledger: {fixed} location(s) reconciled')


warehouse_name()
reconcile_ledger()
env.cr.commit()
print('done  ledger')
