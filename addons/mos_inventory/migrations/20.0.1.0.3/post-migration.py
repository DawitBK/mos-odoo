from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Existing categories valued at Odoo's default standard cost move to the moving weighted average the requirements ask for, and the place
    miscellaneous receipts come from becomes a supplier-type place, so a receipt's cost is what updates the average."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['product.category']._mos_set_average_costing()
    place = env.ref('mos_inventory.location_misc_receipts', raise_if_not_found=False)
    if place and place.usage != 'supplier':
        place.usage = 'supplier'
