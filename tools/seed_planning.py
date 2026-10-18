"""Production planning demo data for MOS: recipes, silo strapping tables and dip readings, a work order, a silo treatment hold.
Run inside the Odoo shell after the other seed scripts. Skips itself when it has run before.
"""

from datetime import timedelta

from odoo import Command, fields

if env['mos.recipe'].search_count([]):
    print('Planning demo data is already there; nothing to do.')
    raise SystemExit(0)

now = fields.Datetime.now()
today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
production = user('mos.production@examplecorp.com')
admin = user('mos.admin@examplecorp.com')
inventory = user('mos.procurement@examplecorp.com')  # holds the inventory manager role in the demo
site = env['mos.site'].search([], limit=1)

# --- strapping tables: height against volume, derived from each silo's capacity -----------------------------------------------
for silo in env['mos.silo'].search([]):
    density = silo.bulk_density or 780.0
    full_volume = silo.capacity_kg / density
    for height in (0, 200, 400, 600, 800, 1000, 1200):
        env['mos.silo.strapping'].create({'silo_id': silo.id, 'height_cm': height, 'volume_m3': round(full_volume * height / 1200.0, 3)})

# --- recipes: the main flours, blended from the wheats the silos hold ---------------------------------------------------------------
wheats = {}
for silo in env['mos.silo'].search([('product_id', '!=', False)]):
    wheats.setdefault(silo.product_id, silo)
outputs = env['mos.milling.output'].search([('kind', '=', 'flour')]).product_id
inputs = env['mos.milling.input'].search([]).product_id
for flour in outputs[:3]:
    # the wheat blend most often used for this flour in the history
    lines = {}
    for run in env['mos.milling.run'].search([('output_ids.product_id', '=', flour)], limit=10):
        total = sum(run.input_ids.mapped('qty_kg')) or 1.0
        for inp in run.input_ids:
            lines[inp.product_id] = lines.get(inp.product_id, 0.0) + inp.qty_kg / total
    if not lines:
        continue
    scale = 100.0 / sum(lines.values())
    shares = {p: round(v * scale, 2) for p, v in lines.items()}
    drift = round(100.0 - sum(shares.values()), 2)
    first = next(iter(shares))
    shares[first] = round(shares[first] + drift, 2)
    recipe = env['mos.recipe'].with_user(production).create(
        {'product_id': flour.id, 'target_extraction_pct': 74.0, 'extraction_tolerance': 3.0, 'grist_tolerance': 12.0, 'line_ids': [Command.create({'wheat_id': p.id, 'share_pct': s}) for p, s in shares.items()]}
    )
    recipe.with_user(admin).action_approve()
env.cr.commit()

# --- dip readings -----------------------------------------------------------------------------------------------------------------
silos = env['mos.silo'].search([], order='id')
for number, silo in enumerate(silos[:4]):
    level = silo.fresh().level_kg
    if level <= 0:
        continue
    density = silo.bulk_density
    height = level / density / (silo.capacity_kg / density) * 1200.0
    factor = (1.0, 0.994, 1.0, 0.96)[number]  # the last one is a real difference
    env['mos.silo.dip'].with_user(production).create({'silo_id': silo.id, 'height_cm': round(min(height * factor, 1200.0), 1), 'date': now - timedelta(hours=2 + number)})

# --- a work order for tomorrow and a silo in fumigation -------------------------------------------------------------------------------
recipe = env['mos.recipe'].search([('state', '=', 'approved')], limit=1)
if recipe:
    centre = env['mos.work.centre'].search([('kind', '=', 'milling')], limit=1)
    order = env['mos.work.order'].with_user(production).create({'site_id': site.id, 'recipe_id': recipe.id, 'planned_kg': 40000, 'date': today + timedelta(days=1), 'shift': 'day', 'work_centre_id': centre.id})
    try:
        order.action_plan()
    except Exception as error:
        env.cr.rollback()
        print('work order plan skipped:', error)
empty = next((s for s in silos if s.fresh().level_kg < 2000), None)
if empty:
    env['mos.silo.hold'].with_user(production).create({'silo_id': empty.id, 'reason': 'fumigation', 'chemical': 'Phosphine tablets, 3 per tonne', 'planned_end': now + timedelta(days=4)})
env.cr.commit()
print('planning ready:', env['mos.recipe'].search_count([]), 'recipes,', env['mos.silo.dip'].search_count([]), 'dips,', env['mos.work.order'].search_count([]), 'work orders')
