from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestProcessMap(TransactionCase):
    def test_every_stage_reads_real_fields(self):
        """A card whose model or field does not exist is dropped quietly for people (as is one they may not read); with every right in place
        none may be dropped."""
        Stage = self.env['mos.process.stage'].sudo()
        wanted = [row[3] for row in Stage._stages() if row[3] in self.env]
        stages = Stage._build()
        self.assertTrue(stages)
        missing = [row[1] for row in Stage._stages() if row[3] in self.env and row[1] not in stages.mapped('name')]
        self.assertFalse(missing, f'stages that could not be counted: {missing}')
        self.assertEqual(len(stages), len(wanted))

    def test_the_lanes_follow_the_work(self):
        stages = self.env['mos.process.stage'].sudo()._build()
        lanes = []
        for stage in stages.sorted('sequence'):
            if stage.lane not in lanes:
                lanes.append(stage.lane)
        self.assertEqual(lanes, ['buy', 'make', 'sell', 'control'])

    def test_a_card_opens_the_records_it_counts(self):
        stage = self.env['mos.process.stage'].sudo()._build().filtered(lambda s: s.res_model == 'mos.silo')[:1]
        action = stage.action_open()
        self.assertEqual(action['res_model'], 'mos.silo')
        self.assertEqual(len(self.env['mos.silo'].sudo().search(action['domain'])), stage.count)

    def test_the_board_is_rebuilt_not_added_to(self):
        admin = new_test_user(self.env, login='pmadmin4', groups='mos_core.group_mos_admin')
        Stage = self.env['mos.process.stage'].with_user(admin)
        first = len(Stage._build())
        Stage._build()
        self.assertEqual(len(Stage.search([('create_uid', '=', admin.id)])), first)
        self.assertGreater(first, 10)

    def test_a_person_sees_only_what_they_may_read(self):
        everyone = new_test_user(self.env, login='pmuser', groups='mos_core.group_mos_user')
        few = len(self.env['mos.process.stage'].with_user(everyone)._build())
        every = len(self.env['mos.process.stage'].sudo()._build())
        self.assertLess(few, every, 'a plain MOS user cannot read orders, so those cards are left out')

    def test_every_state_a_card_names_exists(self):
        """A domain that names a state which does not exist counts nothing and raises nothing: check the values against the field."""
        problems = []
        for lane, name, _what, model, open_domain, _caption, attention_domain, total_domain in self.env['mos.process.stage']._stages():
            if model not in self.env:
                continue
            fields_ = self.env[model]._fields
            for domain in (open_domain, attention_domain, total_domain):
                for leaf in domain:
                    if not (isinstance(leaf, tuple) and len(leaf) == 3) or '.' in leaf[0]:
                        continue
                    field = fields_.get(leaf[0])
                    if field is None:
                        problems.append(f'{name}: {model} has no field {leaf[0]}')
                    elif field.type == 'selection' and leaf[1] in ('=', '!=', 'in', 'not in'):
                        allowed = {key for key, _label in field._description_selection(self.env)}
                        wanted = leaf[2] if isinstance(leaf[2], (list, tuple)) else [leaf[2]]
                        problems += [f'{name}: {leaf[0]} has no state {value!r}' for value in wanted if value not in allowed and value is not False]
        self.assertFalse(problems, problems)

    def test_the_trail_of_an_order_lists_what_belongs_to_it(self):
        supplier = self.env['res.partner'].create({'name': 'Trail supplier', 'supplier_rank': 1})
        product = self.env['product.product'].create({'name': 'Trail item', 'type': 'consu'})
        order = self.env['purchase.order'].create({'partner_id': supplier.id, 'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 10, 'price_unit': 2.0})]})
        action = self.env['mos.process.trail'].open_for(order)
        trail = self.env['mos.process.trail'].browse(action['res_id'])
        self.assertEqual(trail.res_model, 'purchase.order')
        line = trail.line_ids.filtered(lambda l: l.stage == 'Purchase order')
        self.assertEqual((line.res_model, line.res_id), ('purchase.order', order.id))
        self.assertEqual(line.action_open()['res_id'], order.id)
        self.assertEqual(order.action_mos_trail()['res_model'], 'mos.process.trail')

    def test_the_trail_follows_an_order_through_its_documents(self):
        supplier = self.env['res.partner'].create({'name': 'Trail supplier 2', 'supplier_rank': 1})
        product = self.env['product.product'].create({'name': 'Trail wheat', 'type': 'consu'})
        order = self.env['purchase.order'].create({'partner_id': supplier.id, 'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 10, 'price_unit': 2.0})]})
        site = self.env['mos.site'].create({'name': 'Trail mill', 'code': 'TRM', 'warehouse_id': self.env['stock.warehouse'].search([], limit=1).id})
        visit = self.env['mos.gate.visit'].create({'site_id': site.id, 'purpose': 'inbound_po', 'plate': 'TRAIL-1', 'purchase_id': order.id})
        trail = self.env['mos.process.trail'].browse(self.env['mos.process.trail'].open_for(order)['res_id'])
        gate = trail.line_ids.filtered(lambda l: l.stage == 'Truck at the gate')
        self.assertEqual(gate.mapped('res_id'), [visit.id])
        stages = trail.line_ids.mapped('stage')
        self.assertLess(stages.index('Purchase order'), stages.index('Truck at the gate'), 'the order comes before its trucks')
