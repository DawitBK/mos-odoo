{
    'name': 'MOS Role Dashboards',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The approvals dashboard (total, pending, approved, rejected, average approval time with a six-month trend, each figure opening its list) and the mill overview as cards',
    'license': 'LGPL-3',
    'depends': ['mos_approvals', 'mos_platform_plus', 'mos_theme'],
    'data': [
        'security/ir.access.csv',
        'views/mos_dashboards_views.xml',
        'views/mos_overview_cards.xml',
    ],
    'installable': True,
}
