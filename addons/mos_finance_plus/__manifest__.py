{
    'name': 'MOS Finance Extras',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Self-billing, supplier statements, GRNI reconciliation, VAT and withholding registers, journal export for the external accounts, payment runs',
    'license': 'LGPL-3',
    'depends': ['mos_production_plus'],
    'data': [
        'security/ir.access.csv',
        'data/mos_finance_plus_data.xml',
        'views/mos_finance_plus_views.xml',
        'report/mos_finance_plus_reports.xml',
    ],
    'installable': True,
}
