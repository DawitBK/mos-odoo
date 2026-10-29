{
    'name': 'MOS Manufacturing',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Batches and traceability, finished-goods release, packing, fortification dosing, work centres, downtime, cost settlement, production and planner reports',
    'license': 'LGPL-3',
    'depends': ['mos_flows', 'mos_production'],
    'data': [
        'security/mos_manufacturing_security.xml',
        'security/ir.access.csv',
        'data/mos_manufacturing_data.xml',
        'views/mos_manufacturing_views.xml',
        'report/mos_manufacturing_reports.xml',
        'views/mos_parity_views.xml',
        'views/mos_output_cost_views.xml',
    ],
    'installable': True,
}
