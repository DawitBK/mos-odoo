{
    'name': 'MOS Stock Extras',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Lots with batch numbering, supplier lot, expiry and category attributes, lot split and expiry list, transfers between sites with stock in transit, supplier service categories',
    'license': 'LGPL-3',
    'depends': ['mos_planning_plus'],
    'data': [
        'security/ir.access.csv',
        'data/mos_stock_plus_data.xml',
        'views/mos_stock_plus_views.xml',
    ],
    'installable': True,
}
