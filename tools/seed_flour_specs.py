"""Finished-product specifications of the flour pack (FR-QC-026): moisture, ash, protein, wet gluten, falling number, water absorption,
granulation, colour and the net content of the pack, for every flour item, approved. Run in the Odoo shell after the other seed
scripts; an item that already has an approved specification with all nine checks is left alone.
"""

from odoo import Command

# name, unit, min, max  (by flour; limits are typical mill acceptance bands, the quality manager changes them in a new version)
COMMON = [('Moisture %', '%', 10.0, 14.5), ('Ash %', '%', 0.35, 0.80), ('Water absorption %', '%', 56.0, 66.0), ('Granulation, through 150 micron %', '%', 85.0, 100.0), ('Colour (Kent-Jones)', 'KJ', 0.0, 3.5), ('Net content of the pack %', '%', 99.5, 103.0)]
FLOURS = {
    'Semolina': COMMON + [('Protein %', '%', 12.0, 16.0), ('Wet gluten %', '%', 28.0, 38.0), ('Falling number', 's', 250.0, 450.0)],
    'Bread flour 72%': COMMON + [('Protein %', '%', 11.0, 14.0), ('Wet gluten %', '%', 26.0, 36.0), ('Falling number', 's', 220.0, 420.0)],
    'Whole-wheat flour': COMMON + [('Protein %', '%', 11.5, 15.0), ('Wet gluten %', '%', 24.0, 34.0), ('Falling number', 's', 200.0, 400.0)],
}
Spec = env['mos.lab.spec']
made = 0
for name, checks in FLOURS.items():
    product = env['product.product'].search([('name', '=', name)], limit=1)
    if not product:
        continue
    current = Spec.search([('product_id', '=', product.id), ('state', '=', 'approved')], order='id desc', limit=1)
    if current and len(current.line_ids) >= len(checks):
        continue
    top = Spec.search([('product_id', '=', product.id)], order='version desc', limit=1)
    spec = Spec.create({'product_id': product.id, 'version': top.version + 1 if top else 1, 'line_ids': [Command.create({'name': n, 'unit': u, 'min_value': lo, 'max_value': hi}) for n, u, lo, hi in checks]})
    spec.action_approve()
    made += 1
env.cr.commit()
print(f'Flour specifications made: {made}')
