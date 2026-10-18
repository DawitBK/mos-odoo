"""Gate, lab, inventory and control demo data for MOS. Run inside the Odoo shell after the other seed scripts.

Gives the quality rules (grades, deduction bands), a sampling plan, the gate watch list and a guard shift, material gate
passes, reorder levels, a quarantine and a cycle count, the date windows, then runs the exception scan once so the queue
has real findings. Skips itself when it has run before.
"""

from datetime import timedelta

from odoo import Command, fields

if env['mos.date.policy'].search_count([]):
    print('Control demo data is already there; nothing to do.')
    raise SystemExit(0)

today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
guard = user('mos.guard@examplecorp.com')
store = user('mos.storekeeper@examplecorp.com')
buyer = user('mos.buyer@examplecorp.com')
manager = user('mos.procurement@examplecorp.com')
deptmgr = user('mos.deptmanager@examplecorp.com')
site = env['mos.site'].search([], limit=1)

# --- quality rules: a new version of each wheat specification with grades and deduction bands ---------------------
for spec in env['mos.lab.spec'].search([('state', '=', 'approved')]):
    new = spec.action_new_version()
    by_name = {line.name: line for line in new.line_ids}
    if 'Moisture' in by_name:
        by_name['Moisture'].weight = 2.0
        env['mos.lab.deduction.rule'].create({'line_id': by_name['Moisture'].id, 'from_value': 13.51, 'to_value': 14.5, 'weight_deduction_pct': 1.0, 'price_adjust_pct': -1.5})
        env['mos.lab.deduction.rule'].create({'line_id': by_name['Moisture'].id, 'from_value': 14.51, 'to_value': 16.0, 'weight_deduction_pct': 3.0, 'price_adjust_pct': -4.0})
    if 'Foreign matter' in by_name:
        env['mos.lab.deduction.rule'].create({'line_id': by_name['Foreign matter'].id, 'from_value': 2.01, 'to_value': 4.0, 'weight_deduction_pct': 1.5, 'price_adjust_pct': -2.0})
    for name, minimum in (('Grade 1', 100.0), ('Grade 2', 75.0), ('Feed', 0.0)):
        env['mos.lab.grade'].create({'spec_id': new.id, 'name': name, 'min_score': minimum})
    new.action_approve()

wheat = env['product.product'].search([('name', 'ilike', 'wheat')], limit=1)
if wheat and not env['mos.lab.sampling.plan'].search_count([('product_id', '=', wheat.id)]):
    env['mos.lab.sampling.plan'].create(
        {
            'product_id': wheat.id,
            'method': 'Probe at five points of the truck bed, bottom to top, then mix.',
            'line_ids': [Command.create({'from_kg': 0, 'samples': 3}), Command.create({'from_kg': 30000, 'samples': 5})],
        }
    )

# --- gate: watch list, vehicle limits, a guard shift, material passes ---------------------------------------------
env['mos.gate.blacklist'].create({'kind': 'transporter', 'value': 'Swift Haulage', 'severity': 'warn', 'reason': 'Two late arrivals with wet loads this quarter'})
env['mos.gate.blacklist'].create({'kind': 'plate', 'value': 'AA-0000', 'severity': 'block', 'reason': 'Seals found tampered on a previous delivery'})
for vehicle in env['mos.gate.vehicle'].search([]):
    vehicle.max_gross_kg = 56000.0
if guard and not env['mos.gate.shift'].search_count([('state', '=', 'open')]):
    env['mos.gate.shift'].with_user(guard).create({'site_id': site.id})
approver = env['res.users'].search([('login', '=', 'mos.gm@examplecorp.com')], limit=1)
env['res.users'].browse(approver.id).group_ids = [Command.link(env.ref('mos_gate.group_mos_gate_pass_approver').id)]
for kind, carrier, destination, purpose, item, back in (
    ('returnable', 'Workshop van', 'Roller grinding contractor', 'Roller mill rolls re-grinding', 'Roller, 250 mm', 5),
    ('returnable', 'Workshop van', 'Calibration lab', 'Weighbridge test weights', 'Test weight 20 kg set', -2),
    ('final', 'Waste buyer truck', 'Scrap yard', 'Sale of used jute sacks', 'Jute sacks, bale', 0),
):
    gp = env['mos.gate.pass'].with_user(buyer).create(
        {
            'site_id': site.id,
            'kind': kind,
            'carrier': carrier,
            'destination': destination,
            'purpose': purpose,
            'expected_return': today + timedelta(days=back) if kind == 'returnable' else False,
            'line_ids': [Command.create({'name': item, 'quantity': 4})],
        }
    )
    gp.with_user(approver).action_approve()
    if kind == 'returnable' or back == 0:
        gp.with_user(guard).action_out()

# --- inventory: reorder levels, a count and a quarantine ----------------------------------------------------------
flour = env['product.product'].search([('name', 'ilike', 'flour')], limit=2)
for product in flour:
    product.product_tmpl_id.write({'mos_reorder_min': 40000.0, 'mos_reorder_qty': 60000.0})
stock = site.warehouse_id.lot_stock_id
if flour:
    reason = env['mos.reason'].search([('domain', '=', 'status_change')], limit=1) or env['mos.reason'].create({'domain': 'status_change', 'code': 'QC', 'name': 'Quality hold'})
    quant = env['stock.quant'].search([('product_id', '=', flour[0].id), ('location_id', 'child_of', stock.id), ('quantity', '>', 3000)], limit=1)
    if quant:
        change = env['mos.stock.status.change'].with_user(store).create(
            {'site_id': site.id, 'product_id': flour[0].id, 'qty': 1500.0, 'from_status': 'available', 'to_status': 'quarantine', 'reason_id': reason.id, 'location_id': quant.location_id.id, 'note': 'Retest after a high ash reading'}
        )
        change.with_user(manager).action_approve()
        change.with_user(manager).action_apply()
    count = env['mos.cycle.count'].with_user(store).create({'site_id': site.id, 'location_id': stock.id})
    try:
        count.action_start()
    except Exception:  # nothing directly in the main stock location in this demo
        count.unlink()

# --- the other two receiving flows, one delivery each ----------------------------------------------------------------
analyst = user('mos.analyst@examplecorp.com')
supplier = env['res.partner'].search([('mos_supplier_state', '=', 'approved')], limit=1)
if wheat and supplier:
    def order_for(qty):
        po = env['purchase.order'].with_user(buyer).create({'partner_id': supplier.id, 'order_line': [Command.create({'product_id': wheat.id, 'product_qty': qty, 'price_unit': wheat.standard_price or 20.0})]})
        po.with_user(buyer).button_confirm()
        return po

    env.cr.commit()
    try:
        po_c = order_for(30000.0)
        env['mos.gate.visit'].with_user(analyst).lab_intake(po_c, 'ET-C-4471', gross=44000.0, tare=14500.0, driver_name='Tesfaye Alemu', delivery_note='DN-2210', bags=0)
        po_b = order_for(6000.0)
        visit_b = env['mos.gate.visit'].with_user(guard).create({'site_id': site.id, 'plate': 'ET-B-3302', 'purpose': 'inbound_po', 'flow': 'B', 'purchase_id': po_b.id, 'partner_id': supplier.id, 'bags': 120, 'delivery_note': 'DN-9034'})
        visit_b.with_user(store).action_receive_quarantine(5900.0)
    except Exception as error:  # the demo must not stop the rest of the set-up
        env.cr.rollback()
        print('flow B and C demo skipped:', error)

# --- the date windows (after the history is loaded) ---------------------------------------------------------------
for area in ('payables', 'receivables', 'inventory', 'production'):
    env['mos.date.policy'].create({'area': area, 'max_back_days': 7, 'max_forward_days': 1})

# From here on the supervisor-level controls apply (purchase order before weighing, tare and re-weigh confirmation).
env['ir.config_parameter'].sudo().set_str('mos_flows.strict_controls', 'True')
env['mos.exception']._cron_scan()
env.cr.commit()
print('controls ready:', env['mos.exception'].search_count([('state', 'in', ('open', 'assigned'))]), 'open exceptions')
