from . import models


def post_init(env):
    """Make the overview the first screen of everyone who works in MOS."""
    action = env.ref('mos_dashboard.action_mos_overview')
    users = env['res.users'].search([('all_group_ids', 'in', env.ref('mos_core.group_mos_user').ids), ('share', '=', False)])
    users.filtered(lambda u: not u.action_id).write({'action_id': action.id})
