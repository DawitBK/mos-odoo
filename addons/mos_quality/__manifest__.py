{
    'name': 'MOS Quality Extras',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Test methods and parameters, QC policy with waived and skip-lot inspection, calculated parameters, instruments and calibration, sample labels, lab queue',
    'license': 'LGPL-3',
    'depends': ['mos_commercial'],
    'data': [
        'security/ir.access.csv',
        'data/mos_quality_data.xml',
        'views/mos_quality_views.xml',
        'report/mos_quality_reports.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
