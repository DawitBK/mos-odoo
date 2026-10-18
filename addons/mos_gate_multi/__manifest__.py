{
    'name': 'MOS Multi-document Trucks',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'One truck delivering against several purchase orders: intermediate weighings, allocation of the net weight, a receipt per document',
    'license': 'LGPL-3',
    'depends': ['mos_stock_plus'],
    'data': ['security/ir.access.csv', 'views/mos_gate_multi_views.xml'],
    'installable': True,
}
