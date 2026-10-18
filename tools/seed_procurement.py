"""Procurement demo data for MOS. Run inside the Odoo shell after seed_demo.py and seed_business.py.

Approves the demo suppliers with their documents, loads two bank accounts through the three-person change flow,
sets the sourcing policy, adds purchase requests at different stages and turns the approved supplier list on.
Skips itself when it has run before.
"""

from datetime import timedelta

from odoo import Command, fields

if env['mos.sourcing.policy'].search_count([]):
    print('Procurement demo data is already there; nothing to do.')
    raise SystemExit(0)

today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
buyer = user('mos.buyer@examplecorp.com')
manager = user('mos.procurement@examplecorp.com')
finance = user('mos.finance@examplecorp.com')
deptmgr = user('mos.deptmanager@examplecorp.com')
site = env['mos.site'].search([], limit=1)

# --- supplier approval ------------------------------------------------------------------------------------------
suppliers = env['res.partner'].search([('supplier_rank', '>', 0)])
for number, partner in enumerate(suppliers, start=1):
    for kind, label in (('trade_licence', 'TL'), ('tin', 'TIN'), ('vat', 'VAT')):
        env['mos.supplier.document'].create(
            {
                'partner_id': partner.id,
                'doc_type': kind,
                'number': f'{label}-{1000 + number}',
                'issued_on': today - timedelta(days=200),
                'expires_on': today + timedelta(days=(25 if (kind == 'vat' and number == 3) else 120 + 40 * number)),
            }
        )
    partner.with_user(manager).action_approve_supplier()

# --- bank accounts through the change flow ------------------------------------------------------------------------
for partner, account in zip(suppliers[:2], ('1000234567891', '1000987654321')):
    change = env['mos.supplier.bank.change'].with_user(buyer).create({'partner_id': partner.id, 'new_account': account, 'reason': 'First payment account on file'})
    change.with_user(manager).action_verify('Called the finance officer on the number in the signed supply contract')
    change.with_user(finance).action_apply()

# --- policies and the approval row for requests ---------------------------------------------------------------------
env['mos.sourcing.policy'].create({'amount_from': 2000000, 'min_quotes': 3})
env['mos.approval.rule'].create({'doc_type': 'mos.purchase.request', 'sequence': 10, 'amount_from': 50000, 'group_id': env.ref('mos_approvals.group_mos_dept_manager').id, 'sla_hours': 24})
env['mos.approval.rule'].create({'doc_type': 'mos.po.amendment', 'sequence': 10, 'amount_from': 100000, 'group_id': env.ref('mos_approvals.group_mos_finance_manager').id, 'sla_hours': 24})

# --- purchase requests ---------------------------------------------------------------------------------------------
spare = env['product.product'].search([('type', '=', 'consu'), ('purchase_ok', '=', True)], limit=2)
if spare:
    for reason, qty, price in (('Polypropylene flour bags for the packing line', 20000, 18.0), ('Lubricant for the roller mills', 120, 450.0), ('Sieve cloth for the plansifter', 12, 2600.0)):
        request = env['mos.purchase.request'].with_user(buyer).create(
            {
                'site_id': site.id,
                'department': 'Operations',
                'reason': reason,
                'needed_by': today + timedelta(days=14),
                'line_ids': [Command.create({'product_id': spare[0].id, 'qty': qty, 'estimated_price': price})],
            }
        )
        request.with_user(buyer).action_submit()

if suppliers:
    env['ir.config_parameter'].sudo().set_str('mos_procurement.enforce_asl', 'True')
env.cr.commit()
print(f'procurement ready: {len(suppliers)} suppliers approved')
