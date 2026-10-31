from . import models


def post_init(env):
    """A new MOS database belongs to an Ethiopian mill: its money is birr. Done at install (and again when the chart of accounts loads, see
    models/currency.py); an upgrade never changes the currency of a database that already holds entries."""
    env['res.company']._mos_keep_birr()
    # Make the overview the first screen of everyone who works in MOS.
    action = env.ref('mos_dashboard.action_mos_overview')
    users = env['res.users'].search([('all_group_ids', 'in', env.ref('mos_core.group_mos_user').ids), ('share', '=', False)])
    users.filtered(lambda u: not u.action_id).write({'action_id': action.id})
