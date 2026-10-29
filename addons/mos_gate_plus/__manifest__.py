{
    'name': 'MOS Gate Operations',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Gate posts and weighbridges, plate search and expected arrivals, tolerance rules, clearance log with supervisor override, service visits, paper mode with back-entry, gate registers',
    'license': 'LGPL-3',
    'depends': ['mos_quality'],
    'data': [
        'security/mos_gate_plus_security.xml',
        'security/ir.access.csv',
        'data/mos_gate_plus_data.xml',
        'views/mos_gate_plus_views.xml',
        'report/mos_gate_plus_reports.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
