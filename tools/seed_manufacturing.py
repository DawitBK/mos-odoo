"""Manufacturing demo data for MOS: work centres, bag products, a fortified flour, packing, downtime and shift logs, and one
milling run waiting in quality control. Run inside the Odoo shell after the other seed scripts. Skips itself when run before.
"""

from datetime import timedelta

from odoo import Command, fields

if env['mos.work.centre'].search_count([]):
    print('Manufacturing demo data is already there; nothing to do.')
    raise SystemExit(0)

now = fields.Datetime.now()
today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
site = env['mos.site'].search([], limit=1)
production = user('mos.production@examplecorp.com')
analyst = user('mos.analyst@examplecorp.com')
labmgr = user('mos.labmanager@examplecorp.com')

# --- work centres ----------------------------------------------------------------------------------------------------------
centres = {}
for code, name, kind, cap in (('CLN1', 'Cleaning line', 'cleaning', 60000), ('ML1', 'Milling line 1', 'milling', 25000), ('ML2', 'Milling line 2', 'milling', 25000), ('PK1', 'Packing line', 'packing', 8000)):
    centres[code] = env['mos.work.centre'].create({'code': code, 'name': name, 'kind': kind, 'site_id': site.id, 'capacity_kg_h': cap})
for run in env['mos.milling.run'].search([('work_centre_id', '=', False), ('state', '=', 'draft')]):
    run.work_centre_id = centres['ML1']

# --- products: co-product cost shares, bags, premix ---------------------------------------------------------------------------
bran = env['product.product'].search([('name', 'ilike', 'bran')], limit=1)
if bran:
    bran.product_tmpl_id.mos_cost_factor = 0.2
flour = env['product.product'].search([('name', 'ilike', 'bread flour')], limit=1)
unit = env.ref('uom.product_uom_unit')
categ = flour.categ_id if flour else env['product.category'].search([], limit=1)
bag50 = env['product.product'].create({'name': 'Bread flour 50 kg bag', 'type': 'consu', 'is_storable': True, 'uom_id': unit.id, 'categ_id': categ.id, 'standard_price': 30.0, 'list_price': 38.0, 'mos_bag_kg': 50.0})
env['product.product'].create({'name': 'Bread flour 25 kg bag', 'type': 'consu', 'is_storable': True, 'uom_id': unit.id, 'categ_id': categ.id, 'standard_price': 15.5, 'list_price': 19.5, 'mos_bag_kg': 25.0})
env['product.product'].create({'name': 'Bread flour 10 kg bag', 'type': 'consu', 'is_storable': True, 'uom_id': unit.id, 'categ_id': categ.id, 'standard_price': 6.5, 'list_price': 8.0, 'mos_bag_kg': 10.0})
premix = env['product.product'].create({'name': 'Flour fortification premix (iron, folic acid)', 'type': 'consu', 'is_storable': True, 'uom_id': env.ref('uom.product_uom_kgm').id, 'standard_price': 480.0})
stock = site.warehouse_id.lot_stock_id
env['stock.quant'].with_context(inventory_mode=True).create({'product_id': premix.id, 'location_id': stock.id, 'inventory_quantity': 600.0}).action_apply_inventory()
env.cr.commit()

# --- packing from an existing released batch ----------------------------------------------------------------------------------
batch = env['mos.batch'].search([('kind', '=', 'flour'), ('product_id', '=', flour.id), ('state', '=', 'released'), ('remaining_qty', '>', 20000)], order='id', limit=1) if flour else env['mos.batch']
if batch:
    try:
        pack = env['mos.pack.run'].with_user(production).create(
            {
                'site_id': site.id,
                'work_centre_id': centres['PK1'].id,
                'source_batch_id': batch.id,
                'spillage_kg': 42.0,
                'line_ids': [Command.create({'product_id': bag50.id, 'bags': 160}), Command.create({'product_id': bag50.id, 'bags': 40})],
            }
        )
        env['stock.quant'].with_context(inventory_mode=True).create({'product_id': flour.id, 'location_id': stock.id, 'inventory_quantity': max(env['stock.quant']._get_available_quantity(flour, stock), 0) + 12000}).action_apply_inventory()
        pack.with_user(production).action_complete()
        env.cr.commit()
    except Exception as error:
        env.cr.rollback()
        print('packing demo skipped:', error)

# --- downtime and shift logs ---------------------------------------------------------------------------------------------------
reasons = {}
for code, name in (('JAM', 'Roller jam'), ('PWR', 'Power cut'), ('CHG', 'Product changeover'), ('MNT', 'Planned maintenance')):
    reasons[code] = env['mos.reason'].create({'domain': 'downtime', 'code': code, 'name': name})
for days, centre, reason, minutes, planned in ((2, 'ML1', 'JAM', 35, False), (5, 'ML2', 'PWR', 90, False), (9, 'ML1', 'CHG', 25, True), (14, 'PK1', 'MNT', 120, True), (20, 'ML1', 'JAM', 50, False)):
    start = now - timedelta(days=days, hours=6)
    env['mos.downtime'].create({'work_centre_id': centres[centre].id, 'start': start, 'end': start + timedelta(minutes=minutes), 'reason_id': reasons[reason].id, 'planned': planned, 'note': 'Logged by the shift supervisor'})
for days in range(1, 6):
    env['mos.shift.log'].with_user(production).create(
        {'work_centre_id': centres['ML1'].id, 'date': today - timedelta(days=days), 'shift': 'day', 'notes': 'Normal running; sifter 3 cleaned at 14:00.', 'handover': 'Check the bran feeder belt tension at the start of the night shift.'}
    )
env.cr.commit()

# --- fortification and the quality hold, from now on -------------------------------------------------------------------------------
if flour:
    flour.product_tmpl_id.write({'mos_premix_id': premix.id, 'mos_premix_g_per_t': 150.0})
    flour_spec = env['mos.lab.spec'].search([('product_id', '=', flour.id), ('state', '=', 'approved')], limit=1)
    if not flour_spec:
        flour_spec = env['mos.lab.spec'].create({'product_id': flour.id, 'line_ids': [Command.create({'name': 'Ash %', 'unit': '%', 'min_value': 0.4, 'max_value': 0.75}), Command.create({'name': 'Moisture %', 'unit': '%', 'min_value': 10.0, 'max_value': 14.5}), Command.create({'name': 'Wet gluten %', 'unit': '%', 'min_value': 26.0, 'max_value': 36.0})]})
        flour_spec.action_approve()
env['ir.config_parameter'].sudo().set_str('mos_manufacturing.hold_flour', 'True')
silo = next((s_ for s_ in env['mos.silo'].search([], order='id') if s_.fresh().level_kg > 40000 and s_.product_id), env['mos.silo'])
if silo and flour and bran:
    try:
        run = env['mos.milling.run'].with_user(production).create(
            {
                'site_id': site.id,
                'work_centre_id': centres['ML2'].id,
                'run_date': today,
                'shift': 'night',
                'input_ids': [Command.create({'silo_id': silo.id, 'product_id': silo.product_id.id, 'qty_kg': 30000})],
                'output_ids': [Command.create({'product_id': flour.id, 'kind': 'flour', 'qty_kg': 22400}), Command.create({'product_id': bran.id, 'kind': 'bran', 'qty_kg': 6200})],
                'dosing_ids': [Command.create({'premix_id': premix.id, 'actual_kg': 3.4})],
            }
        )
        run.with_user(production).action_complete()
        held = run.batch_ids.filtered(lambda b: b.kind == 'flour')
        insp = env['mos.batch.inspection'].with_user(analyst).create({'batch_id': held.id})
        for line, value in zip(insp.spec_id.line_ids, (0.58, 12.6, 30.5)):
            insp.record_result(line, value)
        insp.action_submit()
        env.cr.commit()
    except Exception as error:
        env.cr.rollback()
        print('held batch demo skipped:', error)
print('manufacturing ready:', env['mos.batch'].search_count([]), 'batches')
