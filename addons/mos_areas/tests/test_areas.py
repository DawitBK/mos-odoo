import json
import pathlib

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

MATRIX = json.loads((pathlib.Path(__file__).parent / 'matrix.json').read_text(encoding='utf-8'))
EVERYONE = {'mos_dashboard.menu_mos_overview', 'mos_process_map.menu_mos_process_map', 'mos_approvals.menu_mos_approvals'}


@tagged('post_install', '-at_install')
class TestWorkAreas(TransactionCase):
    """Each role sees the menus of the areas it works in and not the rest; everyone sees the overview, the process map and their approvals;
    only an administrator sees the configuration."""

    def _top_menus(self, user):
        root = self.env.ref('mos_core.menu_mos_root')
        visible = set(self.env['ir.ui.menu'].with_user(user)._visible_menu_ids())
        return {m.get_external_id()[m.id] for m in root.child_id if m.id in visible}

    def test_every_role_sees_exactly_its_areas(self):
        for role, areas in MATRIX['roles'].items():
            user = new_test_user(self.env, login=f"area_{role.split('.')[1]}", groups=f'mos_core.group_mos_user,{role}')
            expected = EVERYONE | {MATRIX['areas'][a] for a in areas}
            if role == 'mos_core.group_mos_admin':
                expected.add('mos_core.menu_mos_config')
            self.assertEqual(self._top_menus(user), expected, role)

    def test_a_person_with_only_the_base_group_sees_only_what_everyone_sees(self):
        user = new_test_user(self.env, login='area_plain', groups='mos_core.group_mos_user')
        self.assertEqual(self._top_menus(user), EVERYONE)

    def test_the_configuration_is_for_administrators_only(self):
        manager = new_test_user(self.env, login='area_gm', groups='mos_core.group_mos_user,mos_approvals.group_mos_general_manager')
        self.assertNotIn('mos_core.menu_mos_config', self._top_menus(manager))

    def test_every_area_is_worked_in_by_some_role(self):
        used = {a for areas in MATRIX['roles'].values() for a in areas}
        self.assertEqual(used, set(MATRIX['areas']))

    def test_a_person_with_two_roles_sees_both_sets(self):
        user = new_test_user(self.env, login='area_two', groups='mos_core.group_mos_user,mos_gate.group_mos_gate_guard,mos_lab.group_mos_lab_analyst')
        expected = EVERYONE | {MATRIX['areas'][a] for a in ('gate', 'lab', 'receiving')}
        self.assertEqual(self._top_menus(user), expected)
