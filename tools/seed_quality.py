"""Quality demo data for MOS: test methods and parameters, instruments with calibrations, QC policy. Run in the Odoo shell after the
other seed scripts. Skips itself when it has run before.
"""

from datetime import timedelta

from odoo import fields

if env['mos.lab.method'].search_count([]):
    print('Quality demo data is already there; nothing to do.')
    raise SystemExit(0)

today = fields.Date.today()
labmgr = env['res.users'].search([('login', '=', 'mos.labmanager@examplecorp.com')], limit=1)

# --- methods and parameters -------------------------------------------------------------------------------------------------
methods = {}
for code, name, standard in (
    ('ICC-110', 'Moisture, oven drying', 'ICC Standard 110/1'),
    ('ICC-155', 'Wet gluten content', 'ICC Standard 155'),
    ('ICC-107', 'Falling number', 'ICC Standard 107/1'),
    ('ICC-104', 'Ash content', 'ICC Standard 104/1'),
    ('ISO-712', 'Protein, combustion', 'ISO 20483'),
    ('ICC-114', 'Hectolitre mass', 'ICC Standard 114/1'),
):
    methods[code] = env['mos.lab.method'].create({'code': code, 'name': name, 'standard': standard})
for name, unit, method in (('Moisture', '%', 'ICC-110'), ('Wet gluten', '%', 'ICC-155'), ('Falling number', 's', 'ICC-107'), ('Ash', '%', 'ICC-104'), ('Protein', '%', 'ISO-712'), ('Hectolitre weight', 'kg/hl', 'ICC-114')):
    env['mos.lab.parameter'].create({'name': name, 'unit': unit, 'method_id': methods[method].id})

# --- instruments ---------------------------------------------------------------------------------------------------------------
for code, name, kind, interval, last in (
    ('BAL-01', 'Analytical balance', 'balance', 365, 120),
    ('MM-01', 'Grain moisture meter', 'moisture', 180, 60),
    ('OV-01', 'Drying oven 130 C', 'oven', 180, 30),
    ('LM-01', 'Laboratory mill', 'mill', 365, 200),
    ('MM-02', 'Grain moisture meter (spare)', 'moisture', 90, 100),
):
    inst = env['mos.lab.instrument'].create({'code': code, 'name': name, 'kind': kind, 'interval_days': interval})
    env['mos.lab.calibration'].with_user(labmgr).create({'instrument_id': inst.id, 'date': today - timedelta(days=last), 'result': 'pass', 'certificate_ref': f'CAL-{code}-{today.year}', 'by': 'National metrology laboratory'})

# --- policy ---------------------------------------------------------------------------------------------------------------------
for product in env['product.product'].search([('name', 'ilike', 'wheat')], limit=3):
    env['mos.lab.policy'].create({'product_id': product.id, 'inspection_required': True, 'skip_lot': True, 'clean_streak': 8, 'skip_every': 4})
imported = env['product.product'].search([('name', 'ilike', 'hard red')], limit=1)
if imported:
    policy = env['mos.lab.policy'].search([('product_id', '=', imported.id)], limit=1)
    (policy or env['mos.lab.policy'].create({'product_id': imported.id})).write({'coa_required': True, 'skip_lot': False})
env.cr.commit()
print('quality ready:', env['mos.lab.instrument'].search_count([]), 'instruments,', env['mos.lab.policy'].search_count([]), 'policies')
