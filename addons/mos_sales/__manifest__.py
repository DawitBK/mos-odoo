{
    'name': 'MOS Sales',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Sales orders with credit control and approval, delivery orders through the gate, invoices, receipts, cash sessions, ageing (FR-SAL-001..018)',
    'license': 'LGPL-3',
    'depends': ['mos_approvals', 'mos_gate', 'mos_silo', 'sale_stock'],
    'data': [
        'security/mos_sales_security.xml',
        'security/ir.access.csv',
        'data/mos_sales_data.xml',
        'views/mos_sales_views.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
