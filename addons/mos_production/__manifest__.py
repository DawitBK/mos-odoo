{
    'name': 'MOS Milling Production',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Milling runs: wheat drawn from silos, flour and bran produced, extraction rate (FR-MFG-005..015)',
    'license': 'LGPL-3',
    'depends': ['mos_silo'],
    'data': [
        'security/ir.access.csv',
        'data/mos_production_sequences.xml',
        'views/mos_production_views.xml',
    ],
    'installable': True,
}
