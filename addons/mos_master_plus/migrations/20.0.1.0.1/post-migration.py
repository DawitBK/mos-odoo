from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Items that were made before the item master get their material ID, oldest first."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("SELECT id FROM product_template WHERE mos_material_id IS NULL ORDER BY id")
    for (template_id,) in cr.fetchall():
        cr.execute('UPDATE product_template SET mos_material_id = %s WHERE id = %s', (env['ir.sequence'].next_by_code('mos.material'), template_id))
