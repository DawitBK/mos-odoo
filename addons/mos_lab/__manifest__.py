{
    'name': 'MOS Lab and Quality',
    'version': '20.0.1.0.1',
    'category': 'Manufacturing/MOS',
    'summary': 'Versioned specifications, inspections, rule-based deductions, grading, sampling plans, retention samples, certificates of analysis and lab reports',
    'license': 'LGPL-3',
    'depends': ['mos_gate'],
    'data': [
        'security/mos_lab_security.xml',
        'security/ir.access.csv',
        'data/mos_lab_sequences.xml',
        'views/mos_lab_views.xml',
        'views/mos_lab_quality_views.xml',
    ],
    'installable': True,
}
