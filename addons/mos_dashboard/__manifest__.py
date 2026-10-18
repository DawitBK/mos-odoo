{
    'name': 'MOS Overview',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The flour mill at a glance: trucks, inspections, tonnes received, silos, milling and extraction',
    'license': 'LGPL-3',
    'depends': ['mos_inbound', 'mos_production', 'mos_payables', 'mos_sales'],
    'data': [
        'security/ir.access.csv',
        'data/mos_dashboard_data.xml',
        'views/mos_overview_views.xml',
    ],
    'post_init_hook': 'post_init',
    'installable': True,
}
