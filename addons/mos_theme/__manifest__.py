{
    'name': 'MOS Look and Feel',
    'version': '20.0.1.0.0',
    'category': 'Manufacturing/MOS',
    'summary': 'The blue and navy look of the client screens: colours, status pills, section headers, summary cards and tables, for every MOS screen',
    'license': 'LGPL-3',
    'depends': ['mos_core', 'mail'],
    'data': ['views/login.xml'],
    'assets': {
        'web._assets_primary_variables': [('prepend', 'mos_theme/static/src/scss/primary_variables.scss')],
        'web.assets_backend': [
            'mos_theme/static/src/scss/mos_icons.scss',
            'mos_theme/static/src/scss/mos_backend.scss',
            'mos_theme/static/src/scss/mos_shell.scss',
            'mos_theme/static/src/js/chatter_below.js',
            'mos_theme/static/src/js/sidebar.js',
            'mos_theme/static/src/xml/sidebar.xml',
        ],
        'web.assets_frontend': ['mos_theme/static/src/scss/mos_icons.scss', 'mos_theme/static/src/scss/mos_frontend.scss'],
    },
    'installable': True,
}
