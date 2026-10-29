{
    'name': 'MOS Production Planning',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Versioned recipes with grist ratios, work orders, silo dip readings against the ledger, fumigation and treatment holds',
    'license': 'LGPL-3',
    'depends': ['mos_gate_plus'],
    'data': [
        'security/ir.access.csv',
        'data/mos_production_plus_data.xml',
        'views/mos_production_plus_views.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
