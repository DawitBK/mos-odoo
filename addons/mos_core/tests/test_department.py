from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestDepartments(TransactionCase):
    """FR-PLT-007: departments are a hierarchy with a head, and a person belongs to one home department."""

    def setUp(self):
        super().setUp()
        self.Dept = self.env['mos.department']
        self.ops = self.Dept.create({'name': 'Operations'})
        self.mill = self.Dept.create({'name': 'Milling', 'parent_id': self.ops.id})

    def test_the_full_name_follows_the_hierarchy(self):
        self.assertEqual(self.mill.complete_name, 'Operations / Milling')
        self.ops.name = 'Plant'
        self.assertEqual(self.mill.complete_name, 'Plant / Milling')

    def test_a_department_cannot_be_part_of_itself_or_its_own_child(self):
        with self.assertRaises(ValidationError):
            self.ops.parent_id = self.mill

    def test_two_departments_cannot_share_a_name_under_one_parent(self):
        with self.assertRaises(Exception), self.cr.savepoint():
            self.Dept.create({'name': 'Milling', 'parent_id': self.ops.id})

    def test_head_and_people(self):
        boss = self.env['res.users'].create({'name': 'Mill boss', 'login': 'mill_boss'})
        worker = self.env['res.users'].create({'name': 'Miller', 'login': 'miller', 'mos_department_id': self.mill.id})
        self.mill.head_id = boss
        self.assertEqual(self.mill.member_ids, worker)
        self.assertEqual(self.mill.member_count, 1)
        self.assertEqual(self.mill.action_open_members()['domain'], [('mos_department_id', '=', self.mill.id)])

    def test_a_department_with_people_or_sub_departments_cannot_be_archived(self):
        with self.assertRaises(ValidationError):
            self.ops.active = False
        self.env['res.users'].create({'name': 'Miller', 'login': 'miller2', 'mos_department_id': self.mill.id})
        with self.assertRaises(ValidationError):
            self.mill.active = False

    def test_by_name_finds_by_full_or_short_name_and_makes_what_is_missing(self):
        self.assertEqual(self.Dept.by_name('operations / milling'), self.mill)
        self.assertEqual(self.Dept.by_name('Milling'), self.mill)
        made = self.Dept.by_name('Packing')
        self.assertEqual(made.name, 'Packing')
        self.assertFalse(self.Dept.by_name('  '))
