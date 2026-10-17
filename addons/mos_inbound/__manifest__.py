{
    'name': 'MOS Inbound',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Inbound raw-material flow: gate, weighing, inspection and a system-prepared goods receipt',
    'license': 'LGPL-3',
    'depends': ['mos_lab', 'purchase_stock'],
    'data': [
        'security/mos_inbound_security.xml',
        'views/mos_inbound_views.xml',
    ],
    'installable': True,
}
