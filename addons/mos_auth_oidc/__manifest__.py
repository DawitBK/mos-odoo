{
    'name': 'MOS SSO login (OpenID Connect, code flow with PKCE)',
    'version': '20.0.1.0.0',
    'category': 'Hidden/Tools',
    'summary': 'Sign in through an OpenID Connect identity provider; users must already exist (FR-PLT-010, 011); signs out with it',
    'license': 'LGPL-3',
    'depends': ['auth_oauth', 'mos_core'],
    'data': [
        'data/mos_oidc_provider.xml',
        'data/mos_login_screen.xml',
    ],
    'installable': True,
}
