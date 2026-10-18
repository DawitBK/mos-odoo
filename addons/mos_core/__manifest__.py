{
    'name': 'MOS Core',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Sites, scoping, reason codes, hash-chained audit log and posting rules for MOS',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'stock', 'purchase'],
    'data': [
        'security/mos_security.xml',
        'security/ir.access.csv',
        'data/mos_sequences.xml',
        'data/mos_cron.xml',
        'views/mos_core_views.xml',
        'views/mos_menus.xml',
        'views/mos_attachment_views.xml',
    ],
    'application': True,
    'installable': True,
}
