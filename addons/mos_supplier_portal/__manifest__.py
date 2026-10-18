{
    'name': 'MOS Supplier Portal',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'A supplier signs in to see its own orders, promise delivery dates, answer requests for quotation, submit invoices and read its statement',
    'license': 'LGPL-3',
    'depends': ['mos_master_plus', 'auth_signup'],
    'data': ['security/ir.access.csv', 'data/mos_supplier_portal_data.xml', 'views/mos_supplier_portal_views.xml'],
    'installable': True,
}
