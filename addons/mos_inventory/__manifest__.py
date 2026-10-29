{
    'name': 'MOS Inventory Control',
    'version': '20.0.1.0.1',
    'category': 'Manufacturing/MOS',
    'summary': 'Stock adjustments with approval, cycle counts, quarantine and blocked stock, reorder report, archive rules, labels',
    'license': 'LGPL-3',
    'depends': ['mos_procurement'],
    'data': [
        'security/mos_inventory_security.xml',
        'security/ir.access.csv',
        'data/mos_inventory_data.xml',
        'views/mos_inventory_views.xml',
        'report/mos_labels.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
