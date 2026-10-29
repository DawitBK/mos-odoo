"""Lifecycle seed, part 7: the masters (client pages 16-20). Item types, codes and stock levels, the warehouse masters, stores and bins with stock
in them, so the master screens, the capacity bars and the bin and stock cards show something real. Run after the other parts."""

from math import ceil

store_keeper = user('storekeeper')
Location = env['stock.location']
Quant = env['stock.quant'].sudo()


def items():
    plan = {
        'Durum wheat (local)': ('raw', 'RAW-WHT-001'),
        'Bread wheat (local)': ('raw', 'RAW-WHT-002'),
        'Hard red wheat (imported)': ('raw', 'RAW-WHT-003'),
        'Semolina': ('finished', 'FG-SEM-001'),
        'Bread flour 72%': ('finished', 'FG-FLR-072'),
        'Whole-wheat flour': ('finished', 'FG-FLR-WW'),
        'Wheat bran': ('finished', 'BY-BRN-001'),
        'Wheat germ and offal': ('finished', 'BY-GRM-001'),
        'Bread flour 50 kg bag': ('finished', 'FG-BAG-050'),
        'Bread flour 25 kg bag': ('finished', 'FG-BAG-025'),
        'Bread flour 10 kg bag': ('finished', 'FG-BAG-010'),
        'Flour fortification premix (iron, folic acid)': ('raw', 'RAW-PRM-001'),
        'Polypropylene flour bags': ('packaging', 'PKG-PPB-001'),
        'Roller mill lubricant': ('spare', 'SP-LUB-001'),
        'Plansifter sieve cloth': ('spare', 'SP-SIE-001'),
    }
    for name, (kind, code) in plan.items():
        template = env['product.product'].search([('name', '=', name)], limit=1).product_tmpl_id
        if not template:
            continue
        values = {'mos_material_type': kind}
        if not template.default_code:
            values['default_code'] = code
        if kind == 'raw' and template.mos_reorder_min:
            values.update({'mos_safety_stock': round(template.mos_reorder_min * 0.5), 'mos_max_level': round(template.mos_reorder_min * 3)})
        template.write(values)


def warehouses():
    for warehouse in env['stock.warehouse'].search([]):
        held = warehouse.view_location_id._mos_held_kg()
        warehouse.write(
            {
                'mos_type': 'raw' if warehouse.code == 'WH' else 'general',
                'mos_description': 'The mill\'s main store: wheat in the silos, flour and by-products on the floors.' if warehouse.code == 'WH' else 'The depot at Adama, a transit point for flour.',
                'mos_storekeeper_id': store_keeper.id if warehouse.code == 'WH' else False,
                'mos_capacity_kg': ceil(held * 1.6 / 1000.0) * 1000.0 if held else 2000000.0,
                'mos_temperature_controlled': False,
                'mos_humidity_controlled': warehouse.code == 'WH',
            }
        )


STORES = (
    ('RM-A', 'Raw Material Area', 'raw', 400000.0, 'Wheat in bins before it goes to the silos or the mill.', 'Wheat', True, False, False),
    ('FG-A', 'Finished Goods Area', 'finished', 300000.0, 'Bagged and loose flour waiting for dispatch.', 'Flour', False, False, False),
    ('PKG-A', 'Packaging Area', 'packaging', 60000.0, 'Empty bags and pallets.', 'Materials and packaging', False, False, False),
    ('ADD-A', 'Additives Area', 'raw', 20000.0, 'Fortification premix, kept dry.', 'Materials and packaging', True, True, True),
    ('SP-A', 'Spare Parts Area', 'spare', 10000.0, 'Sieve cloth, lubricants and wearing parts.', False, False, False, True),
)

BINS = (
    # code, store, description, capacity kg, item, opening kg
    ('BIN-WHT-01', 'RM-A', 'Bread wheat bin 01', 30000.0, 'Bread wheat (local)', 21500.0),
    ('BIN-WHT-02', 'RM-A', 'Durum wheat bin 02', 30000.0, 'Durum wheat (local)', 12400.0),
    ('BIN-WHT-03', 'RM-A', 'Imported wheat bin 03', 25000.0, 'Hard red wheat (imported)', 0.0),
    ('BIN-FG-01', 'FG-A', 'Semolina bin 01', 20000.0, 'Semolina', 6800.0),
    ('BIN-FG-02', 'FG-A', 'Bread flour 72% bin 02', 20000.0, 'Bread flour 72%', 14250.0),
    ('BIN-BRN-01', 'FG-A', 'Wheat bran bin 01', 15000.0, 'Wheat bran', 9100.0),
    ('BIN-ADD-01', 'ADD-A', 'Premix bin 01', 5000.0, 'Flour fortification premix (iron, folic acid)', 1450.0),
    ('BIN-SP-01', 'SP-A', 'Spare parts shelf 01', 2000.0, False, 0.0),
)


def stores_and_bins():
    warehouse = env['stock.warehouse'].search([('code', '=', 'WH')], limit=1)
    categories = {c.name: c for c in env['product.category'].search([])}
    by_code = {}
    for code, name, kind, capacity, description, category, temperature, humidity, restricted in STORES:
        by_code[code] = Location.search([('mos_kind', '=', 'store'), ('mos_code', '=', code), ('mos_warehouse_id', '=', warehouse.id)], limit=1) or Location.create(
            {
                'name': name,
                'mos_kind': 'store',
                'mos_code': code,
                'mos_warehouse_id': warehouse.id,
                'mos_store_type': kind,
                'mos_capacity_kg': capacity,
                'mos_description': description,
                'mos_storekeeper_id': store_keeper.id,
                'mos_material_category_id': categories[category].id if category else False,
                'mos_temperature_controlled': temperature,
                'mos_humidity_controlled': humidity,
                'mos_restricted_access': restricted,
            }
        )
    for code, store_code, description, capacity, item, opening in BINS:
        bin_location = Location.search([('mos_kind', '=', 'bin'), ('mos_code', '=', code), ('mos_warehouse_id', '=', warehouse.id)], limit=1)
        if not bin_location:
            bin_location = Location.create(
                {
                    'mos_kind': 'bin',
                    'mos_code': code,
                    'mos_warehouse_id': warehouse.id,
                    'location_id': by_code[store_code].id,
                    'mos_description': description,
                    'mos_capacity_kg': capacity,
                    'mos_item_id': env['product.product'].search([('name', '=', item)], limit=1).id if item else False,
                    'mos_remarks': description,
                }
            )
        product_row = env['product.product'].search([('name', '=', item)], limit=1) if item else env['product.product']
        if product_row and opening and not Quant.search([('location_id', '=', bin_location.id), ('product_id', '=', product_row.id)]):
            # the opening stock of a bin is an inventory adjustment, so the bin card and the stock card carry it
            Quant.with_context(inventory_mode=True).create({'product_id': product_row.id, 'location_id': bin_location.id, 'inventory_quantity': opening}).action_apply_inventory()


step('master_items', items)
step('master_warehouses', warehouses)
step('master_stores_and_bins', stores_and_bins)


def multi_site_people():
    """The general manager and the finance officer oversee both sites, so they choose the operation unit they work at after signing in."""
    both = env['mos.site'].search([])
    for name in ('gm', 'finance'):
        person = user(name)
        person.mos_site_ids = [Command.set(both.ids)]


step('multi_site_people', multi_site_people)
