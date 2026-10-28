{
    'name': 'MOS Documents on Records',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Typed attachment slots with a Verify status on purchase requests and orders, deliveries, supplier invoices and payment vouchers; optional approval of every supplier invoice (FR-AP-020); optional deposit slip and verified invoice rules',
    'license': 'LGPL-3',
    'depends': ['mos_quality', 'mos_payables', 'mos_procurement', 'mos_approvals', 'mos_gate', 'mos_theme'],
    'data': [
        'views/mos_documents_views.xml',
    ],
    'installable': True,
}
