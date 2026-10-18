{
    'name': 'MOS Gate',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Gate visits, weigh tickets, watch list, shifts, exit pass, incidents, stored tare, material gate passes and throughput report',
    'license': 'LGPL-3',
    'depends': ['mos_core'],
    'data': [
        'security/mos_gate_security.xml',
        'security/ir.access.csv',
        'data/mos_gate_sequences.xml',
        'views/mos_gate_views.xml',
        'views/mos_gate_control_views.xml',
    ],
    'installable': True,
}
