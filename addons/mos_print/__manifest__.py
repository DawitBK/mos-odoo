{
    'name': 'MOS Printed Documents',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The branded PDFs the requirements list and the first modules left out (FR-PLT-049): goods receipt note, supplier receipt with the deduction breakdown, self-billed voucher, debit note, withholding certificate, rejection note, customer invoice, customer receipt, delivery note',
    'license': 'LGPL-3',
    'depends': ['mos_commercial', 'mos_finance_plus', 'mos_deliveries', 'mos_payables', 'mos_sales'],
    'data': [
        'report/mos_print_reports.xml',
    ],
    'installable': True,
}
