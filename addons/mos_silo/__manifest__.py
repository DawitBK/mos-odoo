{
    'name': 'MOS Silos',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Grain silos with capacity, live level from the stock ledger and fill status (FR-INV-019)',
    'license': 'LGPL-3',
    'depends': ['mos_core'],
    'data': [
        'security/ir.access.csv',
        'views/mos_silo_views.xml',
    ],
    'installable': True,
}
