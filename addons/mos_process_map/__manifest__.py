{
    'name': 'MOS Process Map',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The whole mill on one board, from the supplier to the payment, from the wheat to the flour and from the order to the cash: every stage with what is waiting in it, and a click into its list; and the document trail of an order, every linked document in the order the work happened',
    'license': 'LGPL-3',
    'depends': ['mos_core', 'mos_theme', 'purchase', 'sale'],
    'data': [
        'security/ir.access.csv',
        'views/mos_process_map_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mos_process_map/static/src/scss/process_map.scss',
        ],
    },
    'installable': True,
}
