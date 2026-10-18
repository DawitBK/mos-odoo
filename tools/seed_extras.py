"""Demo data for the later modules: sourcing (RFQ, bag types, a contract), platform (identity-group mapping, idle limit),
planning (routing, work-centre rates, planning suggestions). Run inside the Odoo shell after the other seed scripts. Skips itself
when it has run before; each part is independent so one failing does not stop the others.
"""

from datetime import timedelta

from odoo import Command, fields

if env['mos.bag.type'].search_count([]):
    print('Extras demo data is already there; nothing to do.')
    raise SystemExit(0)

today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
buyer = user('mos.procurement@examplecorp.com')
admin = user('mos.admin@examplecorp.com')
production = user('mos.production@examplecorp.com')
site = env['mos.site'].search([], limit=1)
done = []


def part(name):
    def wrap(fn):
        try:
            fn()
            env.cr.commit()
            done.append(name)
        except Exception as error:  # a demo part must never block the rest
            env.cr.rollback()
            print(f'{name} skipped: {error}')
        return fn

    return wrap


@part('bags')
def bags():
    for name, tare in (('50 kg polypropylene sack', 0.12), ('100 kg jute sack', 0.65), ('25 kg paper bag', 0.08)):
        env['mos.bag.type'].create({'name': name, 'tare_kg': tare})


@part('rfq')
def rfq():
    wheat = env['mos.silo'].search([('product_id', '!=', False)], limit=1).product_id
    suppliers = env['res.partner'].search([('supplier_rank', '>', 0)], limit=3)
    if not (wheat and len(suppliers) >= 2):
        raise ValueError('no wheat or suppliers to quote')
    request = env['mos.rfq'].with_user(buyer).create({'site_id': site.id, 'invited_ids': [Command.set(suppliers.ids)], 'line_ids': [Command.create({'product_id': wheat.id, 'qty': 30000.0})]})
    request.action_send()
    base = wheat.standard_price or 20.0
    for index, partner in enumerate(suppliers):
        env['mos.rfq.quote'].with_user(buyer).create(
            {'rfq_id': request.id, 'partner_id': partner.id, 'lead_days': 4 + index, 'valid_until': today + timedelta(days=14), 'line_ids': [Command.create({'rfq_line_id': request.line_ids.id, 'unit_price': round(base * (1 + 0.03 * index), 2)})]}
        )


@part('contract')
def contract():
    wheat = env['mos.silo'].search([('product_id', '!=', False)], limit=1).product_id
    supplier = env['res.partner'].search([('supplier_rank', '>', 0)], limit=1)
    record = env['mos.purchase.contract'].with_user(buyer).create(
        {'supplier_id': supplier.id, 'product_id': wheat.id, 'qty': 200000.0, 'price': wheat.standard_price or 20.0, 'valid_from': today, 'valid_to': today + timedelta(days=180), 'quality_note': 'Moisture at most 13.5 percent; falling number at least 250.'}
    )
    record.with_user(admin).action_activate()


@part('idp')
def idp():
    lab = env.ref('mos_lab.group_mos_lab_analyst')
    gate = env.ref('mos_gate.group_mos_gate_guard')
    store = env.ref('mos_inbound.group_mos_storekeeper')
    for claim, group in (('mos-lab', lab), ('mos-gate', gate), ('mos-store', store)):
        env['mos.idp.group.map'].create({'idp_group': claim, 'group_id': group.id, 'site_ids': [Command.set(site.ids)]})
    env['ir.config_parameter'].sudo().set_str('mos_platform_plus.idle_timeout_minutes', '120')


@part('routing')
def routing():
    centres = env['mos.work.centre'].search([('kind', '=', 'milling')])
    packing = env['mos.work.centre'].search([('kind', '=', 'packing')], limit=1) or centres[:1]
    centres.write({'labour_rate_h': 120.0, 'overhead_rate_h': 450.0})
    recipe = env['mos.recipe'].search([('state', '=', 'approved')], limit=1)
    if not (recipe and centres):
        raise ValueError('no approved recipe or milling centre')
    record = env['mos.routing'].with_user(production).create(
        {
            'product_id': recipe.product_id.id,
            'op_ids': [
                Command.create({'sequence': 10, 'name': 'Milling', 'work_centre_id': centres[0].id, 'setup_minutes': 30, 'run_rate_kg_h': centres[0].capacity_kg_h or 5000, 'reporting_point': 'Mill floor'}),
                Command.create({'sequence': 20, 'name': 'Packing', 'work_centre_id': packing.id, 'setup_minutes': 10, 'run_rate_kg_h': 8000, 'reporting_point': 'Packing hall'}),
            ],
        }
    )
    record.with_user(admin).action_approve()


@part('suggestions')
def suggestions():
    found = env['mos.mrp.suggestion'].with_user(production).generate()
    print('planning suggestions:', len(found))


@part('master')
def master():
    wheat = env['mos.silo'].search([('product_id', '!=', False)], limit=1).product_id
    env['mos.item.conversion'].create({'product_id': wheat.id, 'unit_name': 'bag', 'factor': 50.0, 'effective_from': today.replace(month=1, day=1)})
    env['mos.item.conversion'].create({'product_id': wheat.id, 'unit_name': 'quintal', 'factor': 100.0, 'effective_from': today.replace(month=1, day=1)})
    for name, kind in (('Transport', 'service'), ('Equipment repair', 'service'), ('Laboratory work', 'service'), ('Packaging materials', 'product'), ('Spare parts', 'product')):
        env['mos.service.category'].create({'name': name, 'kind': kind})
    gm = env.ref('mos_approvals.group_mos_general_manager')
    for doc_type, amount in (('mos.concession', 0.0), ('mos.concession.major', 0.0)):
        env['mos.approval.rule'].create({'doc_type': doc_type, 'amount_from': amount, 'group_id': gm.id})
    env['ir.config_parameter'].sudo().set_str('mos_master_plus.concession_approval', 'True')


@part('lots')
def lots():
    category = env['mos.silo'].search([('product_id', '!=', False)], limit=1).product_id.categ_id
    if not category:
        raise ValueError('the wheat has no category')
    env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Moisture (%)', 'kind': 'number', 'min_value': 6.0, 'max_value': 20.0, 'required': False})
    env['mos.lot.attribute'].create({'category_id': category.id, 'name': 'Origin', 'kind': 'choice', 'choices': 'Arsi, Bale, Gojjam, Shewa, Imported'})
    env['mos.lot.attribute'].create({'category_id': category.id, 'name': "Supplier's certificate", 'kind': 'text'})


print('extras ready:', ', '.join(done) or 'nothing')
