"""Lets the identity provider's console decide who may use MOS and as what. Run inside the Odoo shell (it provides ``env``):

    docker compose exec -T odoo odoo shell -d mos --db_host db --db_user odoo --db_password <password> --no-http < tools/seed_idp_roles.py

The console offers a list of roles for MOS (platform.json of the platform repository, `externalClients.mos`); a person holding one has it
in the `app_roles` claim at every sign-in. This maps each of those roles to the MOS roles the demo staff of the same name hold
(seed_demo.py GROUPS), with access to every site, and:

  * reads the roles from the `app_roles` claim (mos_auth_oidc.groups_claim),
  * lets MOS create the account of a person the console has given a role, at their first sign-in (mos_platform_plus.idp_create_accounts).

The administrator role is NOT mapped, on purpose: MOS never gives it from the identity provider, a MOS administrator grants it inside MOS
(tools/seed_visitor.py does that for the public sandbox login). The console's "admin" role only lets the person in. Safe to run again.
"""

from odoo import Command

# The same roles as seed_demo.py GROUPS, without admin. The key is the role name the console offers.
ROLES = {
    'guard': ['mos_gate.group_mos_gate_guard'],
    'verifier': ['mos_gate.group_mos_gate_verifier'],
    'analyst': ['mos_lab.group_mos_lab_analyst'],
    'labmanager': ['mos_lab.group_mos_lab_manager'],
    'storekeeper': ['mos_inbound.group_mos_storekeeper'],
    'buyer': ['purchase.group_purchase_user', 'mos_core.group_mos_user', 'mos_procurement.group_mos_procurement_user'],
    'salesmanager': ['mos_commercial.group_mos_sales_manager'],
    'gatesup': ['mos_flows.group_mos_gate_supervisor'],
    'production': ['mos_manufacturing.group_mos_production_supervisor'],
    'procurement': ['mos_procurement.group_mos_procurement_manager', 'mos_inventory.group_mos_inventory_manager'],
    'deptmanager': ['mos_approvals.group_mos_dept_manager'],
    'finance': ['mos_approvals.group_mos_finance_manager'],
    'gm': ['mos_approvals.group_mos_general_manager'],
    'apclerk': ['mos_payables.group_mos_ap_clerk'],
    'cashier': ['mos_payables.group_mos_cashier', 'mos_sales.group_mos_sales_cashier'],
    'salesrep': ['mos_sales.group_mos_sales_rep'],
}

sites = env['mos.site'].search([])
maps = env['mos.idp.group.map'].sudo()
made = 0
for role, xmlids in ROLES.items():
    for xmlid in xmlids:
        group = env.ref(xmlid)
        if not maps.search_count([('idp_group', '=', role), ('group_id', '=', group.id)]):
            maps.create({'idp_group': role, 'group_id': group.id, 'site_ids': [Command.set(sites.ids)]})
            made += 1

params = env['ir.config_parameter'].sudo()
params.set_str('mos_auth_oidc.groups_claim', 'app_roles')
params.set_str('mos_platform_plus.idp_create_accounts', '1')
env.cr.commit()
print(f'Identity provider roles mapped: {made} new mapping(s), {maps.search_count([])} in all; roles are read from app_roles and accounts are created at the first sign-in.')
