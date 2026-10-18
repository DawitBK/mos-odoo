{
    'name': 'MOS Operations Screens',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The dialogs and buttons that let people do from the browser what the models already allow: weigh a truck, verify the weights, re-weigh, resolve or dismiss an exception, void a cheque, split a lot, order from a request, call off a contract and more',
    'license': 'LGPL-3',
    'depends': ['mos_supplier_portal'],
    'data': ['security/ir.access.csv', 'views/mos_operations_ui_views.xml', 'views/mos_search_views.xml'],
    'installable': True,
}
