{
    'name': 'MOS Commercial Controls',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Customer approval and credit-limit changes, price floors and discount limits, approved price lists, credit notes, customer returns, proof of delivery, statements',
    'license': 'LGPL-3',
    'depends': ['mos_manufacturing'],
    'data': [
        'security/mos_commercial_security.xml',
        'security/ir.access.csv',
        'data/mos_commercial_data.xml',
        'views/mos_commercial_views.xml',
        'report/mos_commercial_reports.xml',
    ],
    'installable': True,
}
