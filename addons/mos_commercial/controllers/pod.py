import html

from odoo import http
from odoo.http import request

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proof of delivery</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; padding: 16px; background: #f6f7f9; color: #1b2430; }}
main {{ max-width: 460px; margin: 0 auto; background: #fff; border: 1px solid #d9dee5; border-radius: 10px; padding: 18px; }}
h1 {{ font-size: 20px; margin: 0 0 6px; }} p {{ margin: 4px 0 12px; color: #4a5565; }}
label {{ display: block; font-weight: 600; margin: 12px 0 4px; }}
input[type=text] {{ width: 100%; box-sizing: border-box; padding: 10px; font-size: 16px; border: 1px solid #b9c1cc; border-radius: 6px; }}
canvas {{ width: 100%; height: 160px; border: 1px dashed #7a8696; border-radius: 6px; touch-action: none; background: #fcfcfd; }}
button {{ margin-top: 14px; padding: 12px 18px; font-size: 16px; border: 0; border-radius: 6px; background: #1d5fd1; color: #fff; }}
button.secondary {{ background: #e7ebf0; color: #1b2430; margin-left: 8px; }}
.done {{ color: #1c7a3d; font-weight: 600; }}
</style></head><body><main>{body}</main></body></html>"""

FORM = """<h1>Proof of delivery</h1>
<p>{name} · {customer}<br>{product}, {qty} kg</p>
<form method="post" id="f">
<label for="receiver">Receiver's name</label>
<input type="text" id="receiver" name="receiver" required autocomplete="name">
<label>Signature</label>
<canvas id="pad"></canvas>
<input type="hidden" name="signature" id="signature">
<label for="note">Note (damage, shortage)</label>
<input type="text" id="note" name="note" maxlength="200">
<button type="submit">Confirm delivery</button><button type="button" class="secondary" id="clear">Clear</button>
</form>
<script>
const c = document.getElementById('pad'), x = c.getContext('2d'); let down = false, drawn = false;
function size() {{ const r = c.getBoundingClientRect(); c.width = r.width; c.height = r.height; x.lineWidth = 2.5; x.lineCap = 'round'; x.strokeStyle = '#111'; }}
size();
function pos(e) {{ const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }}
c.addEventListener('pointerdown', e => {{ down = true; const [a, b] = pos(e); x.beginPath(); x.moveTo(a, b); }});
c.addEventListener('pointermove', e => {{ if (!down) return; const [a, b] = pos(e); x.lineTo(a, b); x.stroke(); drawn = true; }});
window.addEventListener('pointerup', () => down = false);
document.getElementById('clear').onclick = () => {{ x.clearRect(0, 0, c.width, c.height); drawn = false; }};
document.getElementById('f').onsubmit = e => {{ if (!drawn) {{ e.preventDefault(); alert('Please sign in the box.'); return; }} document.getElementById('signature').value = c.toDataURL('image/png').split(',')[1]; }};
</script>"""


class PodController(http.Controller):
    @http.route('/mos/pod/<string:token>', type='http', auth='public', methods=['GET'], csrf=False)
    def pod_form(self, token, **_kw):
        delivery = request.env['mos.delivery.order'].sudo().pod_for_token(token)
        if not delivery:
            return request.make_response(PAGE.format(body='<h1>This link is no longer valid</h1><p>It was used already or it has expired. Ask the mill for a new one.</p>'), headers=[('Content-Type', 'text/html; charset=utf-8')], status=404)
        body = FORM.format(
            name=html.escape(delivery.name),
            customer=html.escape(delivery.partner_id.display_name or ''),
            product=html.escape(delivery.product_id.display_name or delivery.invoice_id.product_id.display_name or ''),
            qty=round(delivery.loaded_qty or 0),
        )
        return request.make_response(PAGE.format(body=body), headers=[('Content-Type', 'text/html; charset=utf-8')])

    @http.route('/mos/pod/<string:token>', type='http', auth='public', methods=['POST'], csrf=False)
    def pod_submit(self, token, receiver='', signature='', note='', **_kw):
        delivery = request.env['mos.delivery.order'].sudo().pod_for_token(token)
        if not delivery:
            return request.make_response(PAGE.format(body='<h1>This link is no longer valid</h1>'), headers=[('Content-Type', 'text/html; charset=utf-8')], status=404)
        try:
            delivery.record_pod(receiver, signature, note)
        except Exception as error:  # a short, safe message for the person on the phone
            return request.make_response(PAGE.format(body=f'<h1>Not saved</h1><p>{html.escape(str(error.args[0]) if error.args else "Try again")}</p>'), headers=[('Content-Type', 'text/html; charset=utf-8')], status=400)
        return request.make_response(PAGE.format(body='<h1 class="done">Thank you</h1><p>Your delivery has been confirmed.</p>'), headers=[('Content-Type', 'text/html; charset=utf-8')])
