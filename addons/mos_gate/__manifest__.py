{
    'name': 'MOS Gate',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Gate visits and weigh tickets with gap-free numbering and blind verification',
    'license': 'LGPL-3',
    'depends': ['mos_core'],
    'data': [
        'security/mos_gate_security.xml',
        'security/ir.access.csv',
        'views/mos_gate_views.xml',
    ],
    'installable': True,
}
