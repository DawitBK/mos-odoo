{
    'name': 'MOS Payables',
    'version': '20.0.1.0.2',
    'category': 'Manufacturing/MOS',
    'summary': 'Supplier invoices with three-way match, debit notes, payment vouchers, payments, cheque books, ageing (FR-AP-001..019)',
    'license': 'LGPL-3',
    'depends': ['mos_approvals', 'mos_inbound'],
    'data': [
        'security/mos_payables_security.xml',
        'security/ir.access.csv',
        'data/mos_payables_data.xml',
        'views/mos_payables_views.xml',
    ],
    'installable': True,
}
