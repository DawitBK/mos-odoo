{
    'name': 'MOS Procurement',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Purchase requests, supplier approval and documents, bank-change control, sourcing and receipt policies, PO amendments, supplier returns, GRN reassignment',
    'license': 'LGPL-3',
    'depends': ['mos_payables'],
    'data': [
        'security/mos_procurement_security.xml',
        'security/ir.access.csv',
        'data/mos_procurement_data.xml',
        'data/mos_procurement_cron.xml',
        'views/mos_procurement_views.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
