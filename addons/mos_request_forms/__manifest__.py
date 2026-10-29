{
    'name': 'MOS Request and Order Forms',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The purchase requisition as the client draws it (FR-PRC-007): request date, priority, requester ID, remarks, live available stock and quantity to purchase per item, specification and notes; PART 1 / PART 2 layout on the request and the purchase order',
    'license': 'LGPL-3',
    'depends': ['mos_procurement', 'mos_documents', 'mos_theme'],
    'data': [
        'views/mos_request_forms_views.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
