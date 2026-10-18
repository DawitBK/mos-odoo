"""A user who holds every MOS role (and the Odoo roles they imply), for tools/ui-smoke.mjs: it opens every menu, so it must be allowed
to. Not a person: it exists only on a development or demonstration database. Run inside the Odoo shell.
"""

from odoo import Command

privilege = env.ref('mos_core.res_groups_privilege_mos')
groups = env['res.groups'].search([('privilege_id', '=', privilege.id)])
extra = env['res.groups']
for xml_id in ('sales_team.group_sale_manager', 'stock.group_stock_manager', 'purchase.group_purchase_manager', 'account.group_account_invoice'):
    group = env.ref(xml_id, raise_if_not_found=False)
    if group:
        extra |= group
login = 'mos.smoke@examplecorp.com'
user = env['res.users'].with_context(no_reset_password=True).search([('login', '=', login)], limit=1)
vals = {'group_ids': [Command.set((groups | extra | env.ref('base.group_user')).ids)], 'mos_site_ids': [Command.set(env['mos.site'].search([]).ids)], 'password': 'leadfreak'}
if user:
    user.write(vals)
else:
    user = env['res.users'].with_context(no_reset_password=True).create({'name': 'MOS smoke test', 'login': login, 'email': login, **vals})
env.cr.commit()
print('smoke user ready:', login, len(groups), 'MOS roles')
