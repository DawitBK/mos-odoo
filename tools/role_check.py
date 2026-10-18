"""Server-side check, in seconds, that every role can open what its menu bar offers: for each person and each menu they see, load the
views, read the first rows and open the first record exactly as the screen would, under that person's own rights. Run inside the Odoo
shell with PEOPLE (a JSON list of logins) defined first. Prints one line per failure and a total. The browser tools (ui-smoke,
ui-journey) cover how the screens look and behave."""

import json

from odoo.tools.safe_eval import safe_eval

people = json.loads(PEOPLE)
failures = []
checked = 0
for login in people:
    user = env['res.users'].sudo().search([('login', '=', login)], limit=1)
    if not user:
        failures.append((login, '(user)', 'no such user'))
        continue
    uenv = env(user=user.id)
    for item in uenv['ir.ui.menu'].mos_visible_actions():
        kind, aid = item['action'].split(',')
        if not item['complete_name'].startswith('MOS'):
            continue
        action = env[kind].sudo().browse(int(aid))  # the screen reads the action definition with system rights
        checked += 1
        try:
            if kind == 'ir.actions.act_window':
                model = action.res_model
                Model = uenv[model]
                modes = [m for m in (action.view_mode or 'list,form').split(',') if m]
                views = Model.get_views([(False, m) for m in modes] + [(False, 'form')] * (0 if 'form' in modes else 1), options={'action_id': action.id})
                spec = {f: {} for f in views['models'].get(model, {}) if f in Model._fields}
                try:
                    domain = safe_eval(action.domain or '[]', {'uid': user.id})
                except Exception:
                    domain = []  # a domain that needs today's date: the rows are still read, just unfiltered
                rows = Model.web_search_read(domain, spec, limit=3)
                if rows.get('records'):
                    Model.browse(rows['records'][0]['id']).web_read(spec)
            elif kind == 'ir.actions.server':
                groups = action.group_ids
                if groups:
                    if not (groups & user.all_group_ids):
                        raise Exception("You don't have enough access rights to run this action.")
                else:
                    uenv[action.model_id.model].check_access('write')
        except Exception as error:
            failures.append((login, item['complete_name'], str(error).strip().splitlines()[0][:220] if str(error).strip() else type(error).__name__))
for f in failures:
    print('FAIL', *f)
print(f'checked {checked} menu screens for {len(people)} people: {len(failures)} failed')
