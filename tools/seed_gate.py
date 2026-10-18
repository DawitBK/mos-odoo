"""Gate operations demo data for MOS: posts, a weighbridge, weight tolerances, a paper slip book with some slips entered and some
waiting, and the expected arrivals from purchase order dates. Run inside the Odoo shell after the other seed scripts.
"""

from datetime import timedelta

from odoo import fields

if env['mos.gate.post'].search_count([]):
    print('Gate operations demo data is already there; nothing to do.')
    raise SystemExit(0)

now = fields.Datetime.now()
site = env['mos.site'].search([], limit=1)
guard = env['res.users'].search([('login', '=', 'mos.guard@examplecorp.com')], limit=1)
supervisor = env['res.users'].search([('login', '=', 'mos.gatesup@examplecorp.com')], limit=1)

main = env['mos.gate.post'].create({'code': 'G1', 'name': 'Main gate (trucks in and out)', 'site_id': site.id, 'direction': 'both', 'allows': 'goods'})
env['mos.gate.post'].create({'code': 'G2', 'name': 'Contractor gate', 'site_id': site.id, 'direction': 'in', 'allows': 'service'})
env['mos.weighbridge'].create({'code': 'WB1', 'name': 'Weighbridge 1 (80 t)', 'post_id': main.id, 'capacity_kg': 80000, 'division_kg': 20})

env['mos.gate.tolerance'].create({'purpose': 'inbound_po', 'pct': 3.0, 'action': 'warn'})
env['mos.gate.tolerance'].create({'purpose': 'outbound_do', 'pct': 1.0, 'action': 'block'})

book = env['mos.paper.book'].with_user(supervisor or guard).create({'site_id': site.id, 'prefix': 'PS-', 'first_no': 1, 'last_no': 50})
slips = book.slip_ids.sorted('number')
for number, (plate, driver, hours_ago, gross, tare) in enumerate((('AA-31207', 'Yohannes Girma', 30, 46320, 14180), ('OR-77412', 'Mulu Haile', 29, 51200, 15040), ('AM-20981', 'Tadesse Kebede', 27, 43880, 13760))):
    slip = slips[number]
    arrived = now - timedelta(hours=hours_ago)
    slip.write({'plate': plate, 'driver_name': driver, 'arrived_at': arrived, 'exited_at': arrived + timedelta(minutes=70), 'gross_kg': gross, 'tare_kg': tare})
    slip.with_user(guard).action_enter()
for number, (plate, driver, hours_ago) in enumerate((('SO-45018', 'Abdi Yusuf', 6), ('TG-90334', 'Berhane Tesfay', 5))):
    slip = slips[3 + number]
    slip.write({'plate': plate, 'driver_name': driver, 'arrived_at': now - timedelta(hours=hours_ago), 'gross_kg': 40000 + 1000 * number, 'tare_kg': 14000})
slips[6].with_user(supervisor or guard).action_void('Torn in the rain')

env['mos.gate.visit']._cron_expected_arrivals()
env.cr.commit()
print('gate ready:', env['mos.gate.post'].search_count([]), 'gates,', env['mos.paper.slip'].search_count([('state', '=', 'entered')]), 'paper slips entered,', env['mos.gate.visit'].search_count([('expected_auto', '=', True)]), 'expected arrivals')
