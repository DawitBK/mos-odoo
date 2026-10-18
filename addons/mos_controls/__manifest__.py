{
    'name': 'MOS Controls',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Period close and lock dates, backdating windows, date sanity checks, the process exceptions queue and global search',
    'license': 'LGPL-3',
    'depends': ['mos_inventory', 'mos_production'],
    'data': [
        'security/mos_controls_security.xml',
        'security/ir.access.csv',
        'data/mos_controls_data.xml',
        'views/mos_controls_views.xml',
    ],
    'installable': True,
}
