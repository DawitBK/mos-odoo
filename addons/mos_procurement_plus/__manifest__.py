{
    'name': 'MOS Procurement Extras',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Quotation comparison and award, purchase order closure, bag tare, service receipts, supplier registration by invitation, procurement reports',
    'license': 'LGPL-3',
    'depends': ['mos_finance_plus'],
    'data': [
        'security/ir.access.csv',
        'data/mos_procurement_plus_data.xml',
        'views/mos_procurement_plus_views.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
