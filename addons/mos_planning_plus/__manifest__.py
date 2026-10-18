{
    'name': 'MOS Planning Extras',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Versioned routings on the work order, MRP-lite suggestions with pegging, labour and overhead costing with variances, soft stock reservations for sales orders, purchase contracts with call-offs',
    'license': 'LGPL-3',
    'depends': ['mos_platform_plus', 'mos_production_plus', 'mos_commercial'],
    'data': [
        'security/ir.access.csv',
        'data/mos_planning_plus_data.xml',
        'views/mos_planning_plus_views.xml',
    ],
    'installable': True,
}
