{
    'name': 'MOS Approvals',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Approval matrix by amount, site and role, segregation of duties, delegation, escalation, approvals inbox (FR-PLT-023..032, 054)',
    'license': 'LGPL-3',
    'depends': ['mos_core', 'purchase'],
    'data': [
        'security/mos_approvals_security.xml',
        'security/ir.access.csv',
        'data/mos_approvals_data.xml',
        'views/mos_approvals_views.xml',
        'views/purchase_views.xml',
    ],
    'installable': True,
}
