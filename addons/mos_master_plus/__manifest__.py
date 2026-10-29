{
    'name': 'MOS Master Data Extras',
    'version': '20.0.1.0.1',
    'category': 'Manufacturing/MOS',
    'summary': 'Unique tax numbers and duplicate warnings, individual suppliers, item conversions that never change once used, quality concessions through the approval matrix, heavy reports as background jobs',
    'license': 'LGPL-3',
    'depends': ['mos_notify'],
    'data': ['security/ir.access.csv', 'data/mos_master_plus_data.xml', 'views/mos_master_plus_views.xml', 'views/mos_parity_views.xml'],
    'installable': True,
}
