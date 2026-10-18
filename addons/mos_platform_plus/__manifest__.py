{
    'name': 'MOS Platform Extras',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Role dashboards, access review, site switching, identity-group role mapping, session limits, stock position and balance reports, period-close checklist, exception notifications, Ethiopian dates',
    'license': 'LGPL-3',
    'depends': ['mos_procurement_plus', 'mos_auth_oidc', 'mos_dashboard'],
    'data': [
        'security/ir.access.csv',
        'data/mos_platform_plus_data.xml',
        'views/mos_platform_plus_views.xml',
    ],
    'installable': True,
}
