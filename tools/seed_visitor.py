"""Gives the public sandbox login a MOS account with the administrator role. Run inside the Odoo shell (it provides ``env``):

    docker compose exec -T -e MOS_VISITOR_EMAIL=demo.admin@example.com odoo odoo shell -d mos --db_host db --db_user odoo \
        --db_password <password> --no-http < tools/seed_visitor.py

The address comes from MOS_VISITOR_EMAIL (or a VISITOR_EMAIL line placed in front of this script, which is how provision.mjs
passes it). Without one nothing happens. The name defaults to "Demo Administrator" (MOS_VISITOR_NAME / VISITOR_NAME).

MOS never grants the administrator role from the identity provider's groups and never creates accounts at sign-in, so this is the
hand grant an administrator would make: the same single group as the MOS administrator persona in mos-people.json, the same sites,
and NO local password (the account can only sign in through single sign-on). Safe to run again: it only makes sure the account is
active, holds that group and sees those sites.
"""

import os

from odoo import Command

email = (globals().get('VISITOR_EMAIL') or os.environ.get('MOS_VISITOR_EMAIL') or '').strip().lower()
name = globals().get('VISITOR_NAME') or os.environ.get('MOS_VISITOR_NAME') or 'Demo Administrator'
if not email:
    print('No visitor address given (MOS_VISITOR_EMAIL); nothing to do.')
    raise SystemExit(0)

users = env['res.users'].with_context(no_reset_password=True, active_test=False)
admin_group = env.ref('mos_core.group_mos_admin')
persona = users.search([('login', '=', 'mos.admin@examplecorp.com')], limit=1)
sites = persona.mos_site_ids or env['mos.site'].search([])

user = users.search([('login', '=ilike', email)], limit=1)
if user:
    user.write({'active': True, 'group_ids': [Command.link(admin_group.id)]})
    action = 'updated'
else:
    user = users.create(
        {
            'name': name,
            'login': email,
            'email': email,
            'group_ids': [Command.set([admin_group.id])],
        }
    )
    action = 'created'
user.mos_site_ids = [Command.set(sites.ids)]
env.cr.commit()
print(f'MOS account {action}: {user.login} (administrator, {len(sites)} site(s), no local password).')
