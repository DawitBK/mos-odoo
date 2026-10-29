{
    'name': 'MOS Home',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The first screen of MOS: what needs the person today, the day in figures, quick actions, silos, trucks at the gate and the latest milling runs',
    'license': 'LGPL-3',
    'depends': ['mos_dashboards', 'mos_commercial', 'mos_theme', 'mos_gate_plus', 'mos_quality', 'mos_stock_plus', 'mos_counts'],
    'data': [
        'views/mos_sales_links_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'mos_home/static/src/scss/home.scss',
            'mos_home/static/src/js/home.js',
            'mos_home/static/src/xml/home.xml',
        ],
    },
    'installable': True,
}
