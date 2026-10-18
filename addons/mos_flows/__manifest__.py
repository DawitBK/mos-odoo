{
    'name': 'MOS Receiving Flows',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Flow B (packaged goods into quarantine), flow C (lab-led, gate off), automatic receipts, pre-flight checks, receipt exceptions, payables drafted from receipts',
    'license': 'LGPL-3',
    'depends': ['mos_controls'],
    'data': [
        'security/mos_flows_security.xml',
        'security/ir.access.csv',
        'data/mos_flows_data.xml',
        'views/mos_flows_views.xml',
    ],
    'installable': True,
}
