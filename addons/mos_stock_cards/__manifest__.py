{
    'name': 'MOS Bin Card and Stock Card',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The client\'s Bin card and Stock card screens: filters, summary cards, stock by bin and the transaction history with a running balance, all read from the one stock ledger; printable',
    'license': 'LGPL-3',
    'depends': ['mos_platform_plus', 'mos_silo', 'mos_theme'],
    'data': [
        'security/ir.access.csv',
        'views/mos_stock_cards_views.xml',
        'report/mos_stock_cards_report.xml',
    ],
    'installable': True,
}
