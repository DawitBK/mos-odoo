"""Demo data for MOS: a wheat flour mill. Run inside the Odoo shell (it provides ``env``):

    docker compose exec -T odoo odoo shell -d mos --db_host db --db_user odoo --db_password <password> --no-http < tools/seed_demo.py

Creates the mill (site, silos, wheat and flour products, specifications), the staff (logins match the identity provider
accounts in mos-people.json), suppliers, a month of wheat deliveries that went through the real gate, lab and receipt flow,
a month of milling runs with realistic extraction rates, and trucks at every stage today, so every screen has something
on it. It skips itself when silos already exist (drop the database to start over).
"""

import json
import os
import random
from datetime import timedelta

from odoo import Command, fields

random.seed(20261018)  # the same demo every time

PEOPLE_FILE = os.path.join(os.environ.get('MOS_TOOLS_DIR', '/mnt/tools'), 'mos-people.json')
PEOPLE = json.load(open(PEOPLE_FILE)) if os.path.exists(PEOPLE_FILE) else []
GROUPS = {
    'admin': ['mos_core.group_mos_admin'],
    'guard': ['mos_gate.group_mos_gate_guard'],
    'verifier': ['mos_gate.group_mos_gate_verifier'],
    'analyst': ['mos_lab.group_mos_lab_analyst'],
    'labmanager': ['mos_lab.group_mos_lab_manager'],
    'storekeeper': ['mos_inbound.group_mos_storekeeper'],
    'buyer': ['purchase.group_purchase_user', 'mos_core.group_mos_user', 'mos_procurement.group_mos_procurement_user'],
    'production': ['mos_manufacturing.group_mos_production_supervisor'],
    'procurement': ['mos_procurement.group_mos_procurement_manager', 'mos_inventory.group_mos_inventory_manager'],
    'deptmanager': ['mos_approvals.group_mos_dept_manager'],
    'finance': ['mos_approvals.group_mos_finance_manager'],
    'gm': ['mos_approvals.group_mos_general_manager'],
    'apclerk': ['mos_payables.group_mos_ap_clerk'],
    'cashier': ['mos_payables.group_mos_cashier', 'mos_sales.group_mos_sales_cashier'],
    'salesrep': ['mos_sales.group_mos_sales_rep'],
}

if env['mos.silo'].search_count([]):
    print('MOS demo data is already there; nothing to do.')
    raise SystemExit(0)

kg = env.ref('uom.product_uom_kgm')
warehouse = env['stock.warehouse'].search([], limit=1)
company = env.company
company.name = 'Example Flour Mill'

site = env['mos.site'].create({'name': 'Example Flour Mill', 'code': 'MILL', 'warehouse_id': warehouse.id})

# --- people ---------------------------------------------------------------------------------------------------
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
    users.setdefault(person['role'], user)
guard, verifier, analyst = users['guard'], users['verifier'], users['analyst']
manager, store, buyer = users['labmanager'], users['storekeeper'], users['buyer']
deptmgr, finance, gm = users['deptmanager'], users['finance'], users['gm']
apclerk, cashier, salesrep = users['apclerk'], users['cashier'], users['salesrep']

# --- the approval matrix (data, never code) ---------------------------------------------------------------------
G = lambda x: env.ref(x).id
Rule = env['mos.approval.rule']
for seq, lo, group, fallback in ((10, 0, 'mos_approvals.group_mos_dept_manager', None), (20, 30000.01, 'mos_approvals.group_mos_finance_manager', 'mos_approvals.group_mos_general_manager'), (30, 150000.01, 'mos_approvals.group_mos_general_manager', None)):
    Rule.create({'doc_type': 'purchase.order', 'sequence': seq, 'amount_from': lo, 'group_id': G(group), 'fallback_group_id': G(fallback) if fallback else False, 'sla_hours': 24})
Rule.create({'doc_type': 'mos.payment.voucher', 'sequence': 10, 'amount_from': 0, 'group_id': G('mos_approvals.group_mos_finance_manager'), 'sla_hours': 24})
Rule.create({'doc_type': 'mos.payment.voucher', 'sequence': 20, 'amount_from': 200000.01, 'group_id': G('mos_approvals.group_mos_general_manager')})
Rule.create({'doc_type': 'mos.supplier.invoice', 'sequence': 10, 'amount_from': 0, 'group_id': G('mos_approvals.group_mos_finance_manager')})
Rule.create({'doc_type': 'sale.order', 'sequence': 10, 'amount_from': 0, 'group_id': G('mos_approvals.group_mos_general_manager')})
APPROVER_BY_GROUP = {G('mos_approvals.group_mos_dept_manager'): deptmgr, G('mos_approvals.group_mos_finance_manager'): finance, G('mos_approvals.group_mos_general_manager'): gm}


def run_approval(doc):
    """Submit a document and let the right people approve each stage, as they would in the office."""
    doc.action_mos_submit_approval()
    while True:
        step = env['mos.approval.step'].sudo().search([('request_id', '=', doc.mos_approval_request_id.id), ('state', '=', 'pending')], limit=1)
        if not step:
            break
        step.with_user(APPROVER_BY_GROUP[step.group_id.id]).action_approve('Approved')


def confirm_po(po):
    if po._mos_approval_required():
        run_approval(po.with_user(buyer))
    po.with_user(buyer).button_confirm()


# --- catalogue -----------------------------------------------------------------------------------------------
Categ = env['product.category']
all_goods = env.ref('product.product_category_goods', raise_if_not_found=False) or Categ.search([], limit=1)
wheat_cat = Categ.create({'name': 'Wheat', 'parent_id': all_goods.id})
flour_cat = Categ.create({'name': 'Flour', 'parent_id': all_goods.id})
byprod_cat = Categ.create({'name': 'By-products', 'parent_id': all_goods.id})


def product(name, categ, cost, price=None):
    return env['product.product'].create(
        {
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'uom_id': kg.id,
            'categ_id': categ.id,
            'standard_price': cost,
            'list_price': price or cost * 1.3,
            'purchase_ok': categ == wheat_cat,
            'sale_ok': categ != wheat_cat,
        }
    )


durum = product('Durum wheat (local)', wheat_cat, 0.42)
bread = product('Bread wheat (local)', wheat_cat, 0.38)
hrw = product('Hard red wheat (imported)', wheat_cat, 0.47)
semolina = product('Semolina', flour_cat, 0.62)
bread_flour = product('Bread flour 72%', flour_cat, 0.58)
whole_flour = product('Whole-wheat flour', flour_cat, 0.55)
bran = product('Wheat bran', byprod_cat, 0.16)
offal = product('Wheat germ and offal', byprod_cat, 0.12)

for code, name in (('RW-SCALE', 'Scale drift'), ('RW-ENTRY', 'Keying error'), ('RW-TARE', 'Wrong tare')):
    env['mos.reason'].create({'domain': 'reweigh', 'code': code, 'name': name})


def spec(prod, lines):
    s = env['mos.lab.spec'].create(
        {'product_id': prod.id, 'line_ids': [Command.create({'name': n, 'unit': u, 'min_value': lo, 'max_value': hi}) for n, u, lo, hi in lines]}
    )
    s.action_approve()
    return s


COMMON = [('Moisture', '%', 0, 13.5), ('Hectolitre weight', 'kg/hl', 76, 90), ('Foreign matter', '%', 0, 2.0)]
specs = {
    durum: spec(durum, COMMON + [('Protein', '%', 12.5, 18), ('Wet gluten', '%', 28, 40), ('Falling number', 's', 250, 450)]),
    bread: spec(bread, COMMON + [('Protein', '%', 11.0, 16), ('Wet gluten', '%', 26, 38), ('Falling number', 's', 220, 420)]),
    hrw: spec(hrw, COMMON + [('Protein', '%', 12.5, 16.5), ('Wet gluten', '%', 28, 38), ('Falling number', 's', 250, 450)]),
}

# --- silos and their opening stock ---------------------------------------------------------------------------
SILOS = [
    ('S01', 'Durum silo 1', durum, 800_000, 0.62),
    ('S02', 'Durum silo 2', durum, 800_000, 0.41),
    ('S03', 'Bread wheat silo 1', bread, 600_000, 0.70),
    ('S04', 'Bread wheat silo 2', bread, 600_000, 0.35),
    ('S05', 'Imported wheat silo 1', hrw, 400_000, 0.55),
    ('S06', 'Imported wheat silo 2', hrw, 400_000, 0.18),
]
silos = {}
for code, name, prod, cap, fill in SILOS:
    silo = env['mos.silo'].create({'code': code, 'name': name, 'site_id': site.id, 'product_id': prod.id, 'capacity_kg': cap})
    silos[code] = (silo, fill)
    env['stock.quant'].sudo()._update_available_quantity(prod, silo.location_id, cap * fill * 0.9)  # the rest arrives by truck below


def set_level(silo, target):
    delta = target - silo.fresh().level_kg
    if abs(delta) > 0.5:
        env['stock.quant'].sudo()._update_available_quantity(silo.product_id, silo.location_id, delta)


# --- suppliers -----------------------------------------------------------------------------------------------
supplier_names = ['Highland Farmers Cooperative Union', 'Rift Valley Grain Traders', 'Lakeside Agro Cooperative', 'Plains Commodity Brokers', 'Black Sea Grain Imports Ltd']
suppliers = [env['res.partner'].create({'name': n, 'supplier_rank': 1, 'is_company': True}) for n in supplier_names]
DRIVERS = ['Abel Tadesse', 'Chaltu Bekele', 'Dawit Hailu', 'Eden Girma', 'Fitsum Alemu', 'Genet Worku', 'Habtamu Kebede', 'Ifa Gemechu', 'Kebede Mamo', 'Lemlem Asfaw']
HAULIERS = ['Rift Haulage', 'Sunrise Transport', 'Blue Nile Logistics', 'Highland Carriers']


def plate():
    return f"{random.choice(['AA', 'OR', 'AM', 'SN', 'TG'])}-{random.randint(10000, 99999)}"


def pick_silo(prod, need):
    for code, (silo, _fill) in silos.items():
        if silo.product_id == prod and silo.fresh().free_kg >= need:
            return silo
    return None


def truck(prod, supplier, qty, stage, moisture=None):
    po = env['purchase.order'].with_user(buyer).create(
        {'partner_id': supplier.id, 'order_line': [Command.create({'product_id': prod.id, 'product_qty': qty, 'price_unit': prod.standard_price})]}
    )
    confirm_po(po)
    visit = env['mos.gate.visit'].with_user(guard).create(
        {'site_id': site.id, 'plate': plate(), 'purchase_id': po.id, 'partner_id': supplier.id, 'driver_name': random.choice(DRIVERS), 'transporter': random.choice(HAULIERS)}
    )
    if stage == 'arrived':
        return visit
    visit.action_start_weighing()
    tare = random.randrange(14000, 17500, 10)
    gross = tare + qty * random.uniform(0.985, 1.0)
    if stage == 'weighing':
        visit.ticket_id.record_first(round(gross, 1))
        return visit
    visit.ticket_id.record_first(round(gross, 1))
    visit.ticket_id.record_second(float(tare))
    visit.ticket_id.with_user(verifier).verify_blind(round(gross, 1), float(tare))
    if stage == 'weighed':
        return visit
    insp = visit.with_user(analyst).action_open_inspection().with_user(analyst)
    for line in insp.spec_id.line_ids:
        lo, hi = line.min_value, line.max_value
        value = lo + (hi - lo) * random.uniform(0.25, 0.8)
        if line.name == 'Moisture':
            value = moisture if moisture is not None else random.uniform(10.8, 13.1)
        insp.record_result(line, round(value, 2))
    if stage == 'inspecting':
        return visit
    insp.action_submit_results()
    if insp.suggested_outcome == 'fail':
        insp.with_user(manager).decide('reject', note='Out of specification')
    elif random.random() < 0.3:
        insp.with_user(manager).decide('accept_deduction', random.choice([0.5, 1.0, 1.5, 2.0]), 'Slight foreign matter')
    else:
        insp.with_user(manager).decide('accept')
    visit = visit.with_user(guard)
    if insp.decision != 'reject':
        silo = pick_silo(prod, insp.payable_qty)
        if silo:
            visit.sudo().silo_id = silo.id
    visit.action_prepare_grn()
    if stage == 'grn_prepared':
        return visit
    if visit.grn_state == 'prepared':
        visit.with_user(store).action_confirm_grn()
    visit.action_clear_exit()
    visit.action_exit()
    return visit


# A month of deliveries through the real flow (dates are then spread over the month).
history = []
for i in range(36):
    prod = random.choices([durum, bread, hrw], weights=[4, 4, 2])[0]
    sup = suppliers[4] if prod == hrw else random.choice(suppliers[:4])
    qty = random.choice([28000, 30000, 32000, 34000, 36000, 38000, 40000])
    bad = 15.2 if i in (7, 19, 30) else None
    history.append(truck(prod, sup, qty, 'exited', moisture=bad))
now = fields.Datetime.now()
for idx, visit in enumerate(history):
    days = random.randint(1, 27)
    stamp = now - timedelta(days=days, hours=random.randint(0, 9))
    env.cr.execute('UPDATE mos_gate_visit SET arrived_at = %s, exited_at = %s WHERE id = %s', [stamp - timedelta(hours=2), stamp, visit.id])
env['mos.gate.visit'].invalidate_model()

# Trucks on site right now, one at each stage.
live = [
    (hrw, suppliers[4], 36000, 'arrived'),
    (bread, suppliers[1], 32000, 'weighing'),
    (durum, suppliers[0], 38000, 'weighed'),
    (bread, suppliers[2], 34000, 'inspecting'),
    (durum, suppliers[3], 30000, 'grn_prepared'),
]
for prod, sup, qty, stage in live:
    truck(prod, sup, qty, stage)

# --- a month of milling ------------------------------------------------------------------------------------
MIXES = [  # which silos feed a run, and what comes out (flour product, share of the flour)
    (['S01', 'S02'], durum, [(semolina, 0.85), (bread_flour, 0.15)]),
    (['S03', 'S04'], bread, [(bread_flour, 0.88), (whole_flour, 0.12)]),
    (['S05', 'S06', 'S03'], hrw, [(bread_flour, 0.92), (whole_flour, 0.08)]),
]
today = site.business_date()
for back in range(29, 0, -1):
    day = today - timedelta(days=back)
    if day.weekday() == 6 and random.random() < 0.7:
        continue  # Sundays are mostly down days
    for shift in ('day', 'night') if random.random() < 0.75 else ('day',):
        codes, main, outs = random.choice(MIXES)
        wheat = random.randrange(170_000, 265_000, 1000)
        extraction = random.uniform(0.742, 0.775)
        flour = wheat * extraction
        branq = wheat * random.uniform(0.180, 0.205)
        offalq = wheat * random.uniform(0.008, 0.014)
        inputs = []
        remaining = wheat
        for code in codes:
            silo = silos[code][0]
            part = round(remaining if code == codes[-1] else wheat / len(codes))
            set_level(silo, max(silo.fresh().level_kg, part + 5000))  # the history assumes the silo was stocked
            inputs.append(Command.create({'silo_id': silo.id, 'product_id': silo.product_id.id, 'qty_kg': part}))
            remaining -= part
        outputs = [Command.create({'product_id': p.id, 'kind': 'flour', 'qty_kg': round(flour * share)}) for p, share in outs]
        outputs += [Command.create({'product_id': bran.id, 'kind': 'bran', 'qty_kg': round(branq)}), Command.create({'product_id': offal.id, 'kind': 'offal', 'qty_kg': round(offalq)})]
        run = env['mos.milling.run'].create({'site_id': site.id, 'run_date': day, 'shift': shift, 'input_ids': inputs, 'output_ids': outputs})
        run.action_complete()

# Today: the morning run is done, the night run is planned.
silo = silos['S03'][0]
set_level(silo, max(silo.fresh().level_kg, 260_000))
done_today = env['mos.milling.run'].create(
    {
        'site_id': site.id,
        'run_date': today,
        'shift': 'day',
        'input_ids': [Command.create({'silo_id': silo.id, 'product_id': bread.id, 'qty_kg': 210_000})],
        'output_ids': [
            Command.create({'product_id': bread_flour.id, 'kind': 'flour', 'qty_kg': 150_800}),
            Command.create({'product_id': whole_flour.id, 'kind': 'flour', 'qty_kg': 7_900}),
            Command.create({'product_id': bran.id, 'kind': 'bran', 'qty_kg': 42_000}),
            Command.create({'product_id': offal.id, 'kind': 'offal', 'qty_kg': 3_100}),
        ],
    }
)
done_today.action_complete()
env['mos.milling.run'].create(
    {'site_id': site.id, 'run_date': today, 'shift': 'night', 'input_ids': [Command.create({'silo_id': silos['S01'][0].id, 'product_id': durum.id, 'qty_kg': 220_000})]}
)

# --- put the silos at believable levels ------------------------------------------------------------------------
for code, (silo, fill) in silos.items():
    set_level(silo, silo.capacity_kg * fill)

env.cr.commit()
print(
    'MOS demo data ready:',
    env['mos.gate.visit'].search_count([]), 'trucks,',
    env['mos.lab.inspection'].search_count([]), 'inspections,',
    env['mos.milling.run'].search_count([]), 'milling runs,',
    env['mos.silo'].search_count([]), 'silos.',
)
