from odoo import Command
from odoo.tests import TransactionCase, tagged

REPORTS = {
    'mos_print.report_grn': 'stock.picking',
    'mos_print.report_supplier_receipt': 'mos.gate.visit',
    'mos_print.report_rejection_note': 'mos.gate.visit',
    'mos_print.report_self_billed_voucher': 'mos.supplier.invoice',
    'mos_print.report_debit_note': 'mos.debit.note',
    'mos_print.report_wht_certificate': 'mos.wht.certificate',
    'mos_print.report_customer_invoice': 'mos.customer.invoice',
    'mos_print.report_customer_receipt': 'mos.receipt',
    'mos_print.report_delivery_note': 'mos.delivery.order',
}


@tagged('post_install', '-at_install', 'mos_print')
class TestPrintedDocuments(TransactionCase):
    """FR-PLT-049: every document the requirement lists has a branded PDF. Each template is rendered against an empty record of its
    model, which fails on any field name that does not exist, and the report action must be bound to its model's Print menu."""

    def test_every_listed_document_has_a_bound_report(self):
        for name, model in REPORTS.items():
            report = self.env['ir.actions.report']._get_report_from_name(name)
            self.assertTrue(report, f'{name} is not a report action')
            self.assertEqual(report.model, model)
            self.assertEqual(report.binding_model_id.model, model, f'{name} is not on the Print menu of {model}')

    def test_every_template_renders(self):
        product = self.env['product.product'].create({'name': 'Print test wheat'})
        # Rows in every table the templates loop over, so a wrong field name inside a row fails here and not on the first real print.
        rows = {
            'stock.picking': {'move_ids': [Command.create({'product_id': product.id, 'product_uom_qty': 1, 'quantity': 1})]},
            'mos.gate.visit': {'ticket_ids': [Command.create({'first_weight': 1000, 'second_weight': 400})], 'inspection_ids': [Command.create({'decision': 'accepted'})]},
            'mos.supplier.invoice': {'line_ids': [Command.create({'description': 'Wheat', 'qty': 1, 'unit_price': 10})]},
        }
        for name, model in REPORTS.items():
            doc = self.env[model].new(rows.get(model, {}))
            values = {'docs': doc, 'doc_ids': [], 'doc_model': model, 'visits': {doc.id: doc.env['mos.gate.visit'].new({})}, 'res_company': self.env.company, 'company': self.env.company, 'mos_qr': lambda d: 'MOS|test'}
            html = self.env['ir.qweb']._render(name, values)
            self.assertTrue(str(html).strip(), f'{name} rendered nothing')

    def test_the_documents_carry_the_company_name(self):
        self.env.company.name = 'Print Test Mills'
        doc = self.env['mos.debit.note'].new({})
        html = str(self.env['ir.qweb']._render('mos_print.report_debit_note', {'docs': doc, 'doc_ids': [], 'doc_model': 'mos.debit.note', 'res_company': self.env.company, 'company': self.env.company, 'mos_qr': lambda d: 'MOS|test'}))
        self.assertIn('Print Test Mills', html)

    def test_the_qr_value_carries_the_document_and_a_hash_only_this_database_can_make(self):
        report = self.env['ir.actions.report']
        note = self.env['mos.debit.note'].new({})
        first = report._mos_qr_value(note)
        self.assertTrue(first.startswith('MOS|mos.debit.note|'))
        self.assertEqual(first, report._mos_qr_value(note), 'the same document gives the same code')
        self.env['ir.config_parameter'].sudo().set_str('database.secret', 'another-database')
        self.assertNotEqual(first.split('|')[-1], report._mos_qr_value(note).split('|')[-1])

    def test_a_real_print_goes_through_the_rendering_context(self):
        report = self.env['ir.actions.report']._get_report_from_name('mos_print.report_debit_note')
        context = report._get_rendering_context(report, [], None)
        self.assertTrue(callable(context['mos_qr']))
