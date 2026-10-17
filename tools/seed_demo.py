"""Demo data for MOS on Odoo. Run inside the Odoo shell (it provides ``env``):

    docker compose exec -T odoo odoo shell -d mos --db_host db --db_user odoo --db_password <password> --no-http < tools/seed_demo.py

Creates a mill site, the MOS staff (logins match the identity provider accounts in mos-people.json), a wheat
supplier, products with approved specifications, purchase orders, and gate visits at every stage, so the lists
and dashboards are not empty. Safe to run twice: it skips what exists.
"""

import json
import os

from odoo import Command

ROOT = os.environ.get('MOS_TOOLS_DIR', '/mnt/tools')
PEOPLE_FILE = os.path.join(ROOT, 'mos-people.json')
PEOPLE = json.load(open(PEOPLE_FILE)) if os.path.exists(PEOPLE_FILE) else []

GROUPS = {
    'admin': ['mos_core.group_mos_admin'],
    'guard': ['mos_gate.group_mos_gate_guard'],
    'verifier': ['mos_gate.group_mos_gate_verifier'],
    'analyst': ['mos_lab.group_mos_lab_analyst'],
    'labmanager': ['mos_lab.group_mos_lab_manager'],
    'storekeeper': ['mos_inbound.group_mos_storekeeper'],
    'buyer': ['purchase.group_purchase_user', 'mos_core.group_mos_user'],
}

warehouse = env['stock.warehouse'].search([], limit=1)
site = env['mos.site'].search([('code', '=', 'MILL')]) or env['mos.site'].create(
    {'name': 'Example Flour Mill', 'code': 'MILL', 'warehouse_id': warehouse.id}
)

# --- people -----------------------------------------------------------------------------------------
users = {}
for person in PEOPLE:
    user = env['res.users'].search([('login', '=', person['login'])])
    if not user:
        user = env['res.users'].with_context(no_reset_password=True).create(
            {
                'name': f"{person['given']} {person['family']}",
                'login': person['login'],
                'email': person['login'],
                'group_ids': [Command.set([env.ref(g).id for g in GROUPS[person['role']]])],
            }
        )
    user.mos_site_ids = [Command.link(site.id)]
    users[person['role']] = user

# --- reference data ----------------------------------------------------------------------------------
for code, name in (('RW-SCALE', 'Scale drift'), ('RW-ENTRY', 'Keying error')):
    if not env['mos.reason'].search([('domain', '=', 'reweigh'), ('code', '=', code)]):
        env['mos.reason'].create({'domain': 'reweigh', 'code': code, 'name': name})

kg = env.ref('uom.product_uom_kgm')


def product(name, cost):
    found = env['product.product'].search([('name', '=', name)], limit=1)
    return found or env['product.product'].create(
        {'name': name, 'type': 'consu', 'is_storable': True, 'uom_id': kg.id, 'standard_price': cost, 'purchase_ok': True}
    )


def approved_spec(prod, lines):
    spec = env['mos.lab.spec'].in_force(prod)
    if spec:
        return spec
    spec = env['mos.lab.spec'].create(
        {'product_id': prod.id, 'line_ids': [Command.create({'name': n, 'unit': u, 'min_value': lo, 'max_value': hi}) for n, u, lo, hi in lines]}
    )
    spec.action_approve()
    return spec


wheat = product('Durum wheat', 24.0)
maize = product('Yellow maize', 18.0)
approved_spec(wheat, [('Moisture', '%', 0, 13.5), ('Hectolitre weight', 'kg/hl', 76, 90), ('Foreign matter', '%', 0, 2.0), ('Protein', '%', 11.5, 18)])
approved_spec(maize, [('Moisture', '%', 0, 14.0), ('Aflatoxin', 'ppb', 0, 10), ('Broken kernels', '%', 0, 3.0)])

suppliers = []
for name in ('Highland Farmers Union', 'Rift Valley Grain Traders', 'Lakeside Cooperative'):
    partner = env['res.partner'].search([('name', '=', name)], limit=1) or env['res.partner'].create({'name': name, 'supplier_rank': 1})
    suppliers.append(partner)

# --- purchase orders and gate traffic ----------------------------------------------------------------
buyer = users.get('buyer') or env.user
existing = env['mos.gate.visit'].search_count([])
if not existing:
    plan = [
        # supplier index, product, ordered kg, plate, stage
        (0, wheat, 60000, 'AA-48213', 'exited'),
        (1, wheat, 45000, 'AA-77620', 'exited'),
        (2, maize, 38000, 'OR-31845', 'exited'),
        (0, wheat, 50000, 'AA-91544', 'grn_prepared'),
        (1, maize, 42000, 'AM-20977', 'inspecting'),
        (2, wheat, 40000, 'OR-66013', 'weighed'),
        (0, maize, 36000, 'AA-15502', 'weighing'),
        (1, wheat, 30000, 'SN-44218', 'arrived'),
    ]
    guard, verifier = users['guard'], users['verifier']
    analyst, manager, store = users['analyst'], users['labmanager'], users['storekeeper']
    for i, (s, prod, qty, plate, stage) in enumerate(plan):
        po = env['purchase.order'].with_user(buyer).create(
            {'partner_id': suppliers[s].id, 'order_line': [Command.create({'product_id': prod.id, 'product_qty': qty, 'price_unit': prod.standard_price})]}
        )
        po.with_user(buyer).button_confirm()
        visit = env['mos.gate.visit'].with_user(guard).create({'site_id': site.id, 'plate': plate, 'purchase_id': po.id, 'partner_id': suppliers[s].id, 'driver_name': ['Abel', 'Chaltu', 'Dawit', 'Eden', 'Fitsum', 'Genet', 'Habtamu', 'Ife'][i], 'transporter': 'Example Haulage'})
        if stage == 'arrived':
            continue
        visit.action_start_weighing()
        if stage == 'weighing':
            visit.ticket_id.record_first(qty * 1.0 + 15000)
            continue
        gross, tare = qty + 15000.0 + i * 40, 15000.0 + i * 40
        visit.ticket_id.record_first(gross)
        visit.ticket_id.record_second(tare)
        visit.ticket_id.with_user(verifier).verify_blind(gross, tare)
        if stage == 'weighed':
            continue
        insp = visit.with_user(analyst).action_open_inspection().with_user(analyst)
        spec = insp.spec_id
        for line in spec.line_ids:
            midpoint = (line.min_value + line.max_value) / 2
            insp.record_result(line, round(midpoint if line.max_value else 0.0, 2))
        if stage == 'inspecting':
            continue
        insp.action_submit_results()
        insp.with_user(manager).decide('accept_deduction' if i % 2 else 'accept', 1.5 if i % 2 else 0.0, '')
        visit = visit.with_user(guard)
        visit.action_prepare_grn()
        if stage == 'grn_prepared':
            continue
        visit.with_user(store).action_confirm_grn()
        visit.action_clear_exit()
        visit.action_exit()

env.cr.commit()
print('MOS demo data ready:', env['mos.gate.visit'].search_count([]), 'visits,', env['mos.lab.inspection'].search_count([]), 'inspections')
