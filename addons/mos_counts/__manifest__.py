{
    'name': 'MOS Count Sheets and Review',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'Blind counting, a printable count sheet, import of counts from a spreadsheet, a recount by a different person when a variance is large, and a review that can ask for changes (FR-INV-009)',
    'license': 'LGPL-3',
    'depends': ['mos_inventory', 'mos_theme'],
    'data': [
        'security/ir.access.csv',
        'views/mos_counts_views.xml',
        'report/mos_counts_report.xml',
        'views/mos_parity_views.xml',
    ],
    'installable': True,
}
