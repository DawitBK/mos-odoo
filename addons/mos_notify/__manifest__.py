{
    'name': 'MOS Supplier Notices',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'E-mail to suppliers: arrival acknowledged, rejection note with results, receipt with deductions, payment advice; queued until a mail server exists',
    'license': 'LGPL-3',
    'depends': ['mos_gate_multi'],
    'data': ['security/ir.access.csv', 'views/mos_notify_views.xml'],
    'installable': True,
}
