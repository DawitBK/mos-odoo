{
    'name': 'MOS Supplier Delivery Entry',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The supplier delivery entry screen (FR-PRC-023): delivery note, supplier and items taken from the purchase order, declared quantities with totals per unit, approve to inspection or reject with a reason',
    'license': 'LGPL-3',
    'depends': ['mos_flows', 'mos_gate_multi', 'mos_theme'],
    'data': [
        'security/ir.access.csv',
        'views/mos_deliveries_views.xml',
    ],
    'installable': True,
}
