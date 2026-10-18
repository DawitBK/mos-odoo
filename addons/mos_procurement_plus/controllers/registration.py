import html

from odoo import http
from odoo.http import request

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supplier registration</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; padding: 16px; background: #f6f7f9; color: #1b2430; }}
main {{ max-width: 520px; margin: 0 auto; background: #fff; border: 1px solid #d9dee5; border-radius: 10px; padding: 18px; }}
h1 {{ font-size: 20px; margin: 0 0 6px; }} p {{ margin: 4px 0 12px; color: #4a5565; }}
label {{ display: block; font-weight: 600; margin: 12px 0 4px; }}
input, textarea {{ width: 100%; box-sizing: border-box; padding: 10px; font-size: 16px; border: 1px solid #b9c1cc; border-radius: 6px; font-family: inherit; }}
button {{ margin-top: 16px; padding: 12px 18px; font-size: 16px; border: 0; border-radius: 6px; background: #1d5fd1; color: #fff; }}
.done {{ color: #1c7a3d; font-weight: 600; }} .err {{ color: #b3261e; }}
</style></head><body><main>{body}</main></body></html>"""

FORM = """<h1>Register as a supplier</h1>
<p>Invitation for {invited}. We will check your documents and contact you.</p>
{error}
<form method="post">
<label for="name">Company or trading name</label><input id="name" name="name" required value="{name}">
<label for="vat">TIN</label><input id="vat" name="vat" required value="{vat}">
<label for="email">Email</label><input id="email" name="email" type="email" value="{email}">
<label for="phone">Phone</label><input id="phone" name="phone" value="{phone}">
<label for="address">Address</label><input id="address" name="address" value="{address}">
<label for="activity">What do you supply?</label><textarea id="activity" name="activity" rows="3">{activity}</textarea>
<button type="submit">Send</button>
</form>"""


def _page(body, status=200):
    return request.make_response(PAGE.format(body=body), headers=[('Content-Type', 'text/html; charset=utf-8')], status=status)


class SupplierRegistration(http.Controller):
    @http.route('/mos/supplier/register/<string:token>', type='http', auth='public', methods=['GET'], csrf=False)
    def form(self, token, **_kw):
        invite = request.env['mos.supplier.invite'].sudo().for_token(token)
        if not invite:
            return _page('<h1>This link is no longer valid</h1><p>It was used already or it has expired. Ask the mill for a new one.</p>', 404)
        return _page(FORM.format(invited=html.escape(invite.name), error='', name='', vat='', email=html.escape(invite.email or ''), phone='', address='', activity=''))

    @http.route('/mos/supplier/register/<string:token>', type='http', auth='public', methods=['POST'], csrf=False)
    def submit(self, token, **post):
        invite = request.env['mos.supplier.invite'].sudo().for_token(token)
        if not invite:
            return _page('<h1>This link is no longer valid</h1>', 404)
        try:
            invite.register({k: post.get(k, '') for k in ('name', 'vat', 'email', 'phone', 'address', 'activity')})
        except Exception as error:
            message = html.escape(str(error.args[0]) if error.args else 'Please check the form')
            values = {k: html.escape(post.get(k, '')) for k in ('name', 'vat', 'email', 'phone', 'address', 'activity')}
            return _page(FORM.format(invited=html.escape(invite.name), error=f'<p class="err">{message}</p>', **values), 400)
        return _page('<h1 class="done">Thank you</h1><p>Your registration has been received. The mill will contact you.</p>')
