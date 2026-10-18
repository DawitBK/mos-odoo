from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestReports(TransactionCase):
    """Each printed document renders with the facts that matter on it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        warehouse = env['stock.warehouse'].search([], limit=1)
        cls.site = env['mos.site'].create({'name': 'Mill', 'code': 'MILLR', 'warehouse_id': warehouse.id})
        cls.verifier = new_test_user(env, login='rverifier', groups='mos_gate.group_mos_gate_verifier')
        cls.verifier.mos_site_ids = [Command.set(cls.site.ids)]

    def _html(self, name, records):
        html, _ = self.env['ir.actions.report']._render_qweb_html(name, records.ids)
        return html.decode()

    def _visit(self):
        visit = self.env['mos.gate.visit'].create({'site_id': self.site.id, 'plate': 'RP-4411', 'driver_name': 'Yonas', 'transporter': 'Blue Nile'})
        visit.action_start_weighing()
        ticket = visit.ticket_id
        ticket.record_first(42000.0)
        ticket.record_second(15000.0)
        ticket.with_user(self.verifier).verify_blind(42000.0, 15000.0)
        return visit

    def test_weigh_ticket_and_exit_pass(self):
        visit = self._visit()
        html = self._html('mos_reports.report_weigh_ticket', visit.ticket_id)
        self.assertIn(visit.ticket_id.name, html)
        self.assertIn('RP-4411', html)
        self.assertIn('27000', html)
        with patch.object(self.registry['mos.gate.visit'], '_exit_blockers', lambda rec: []):
            visit.action_clear_exit()
        html = self._html('mos_reports.report_exit_pass', visit)
        self.assertIn(visit.exit_code, html)
        self.assertIn('barcode', html)

    def test_gate_pass(self):
        gp = self.env['mos.gate.pass'].create(
            {
                'site_id': self.site.id,
                'kind': 'returnable',
                'carrier': 'Workshop van',
                'purpose': 'Re-grinding',
                'expected_return': fields.Date.today(),
                'line_ids': [Command.create({'name': 'Roller 250 mm', 'quantity': 2})],
            }
        )
        html = self._html('mos_reports.report_gate_pass', gp)
        self.assertIn(gp.name, html)
        self.assertIn('Roller 250 mm', html)
        self.assertIn('Re-grinding', html)

    def test_voucher_renders(self):
        supplier = self.env['res.partner'].create({'name': 'Print Supplier', 'supplier_rank': 1})
        voucher = self.env['mos.payment.voucher'].create({'supplier_id': supplier.id})
        html = self._html('mos_reports.report_voucher', voucher)
        self.assertIn(voucher.name, html)
        self.assertIn('Print Supplier', html)
