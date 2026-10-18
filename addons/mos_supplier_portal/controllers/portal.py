import html
from datetime import timedelta
from urllib.parse import quote

from psycopg2 import IntegrityError

from odoo import fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

ROOT = '/mos/supplier/portal'

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supplier portal</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; background: #f6f7f9; color: #1b2430; }}
header {{ background: #14335e; color: #fff; padding: 12px 16px; display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }}
header a {{ color: #cfe0ff; text-decoration: none; }} header strong {{ margin-right: auto; }}
main {{ max-width: 920px; margin: 16px auto; padding: 0 16px; }}
section {{ background: #fff; border: 1px solid #d9dee5; border-radius: 10px; padding: 16px; margin-bottom: 16px; overflow-x: auto; }}
h1 {{ font-size: 21px; margin: 0 0 10px; }} h2 {{ font-size: 17px; margin: 0 0 8px; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ text-align: left; padding: 7px 9px; border-bottom: 1px solid #e6e9ee; font-size: 14px; }}
td.n, th.n {{ text-align: right; }}
label {{ display: block; font-weight: 600; margin: 10px 0 4px; }}
input, select, textarea {{ box-sizing: border-box; padding: 9px; font-size: 15px; border: 1px solid #b9c1cc; border-radius: 6px; font-family: inherit; }}
input[type=text], input[type=number], input[type=date], select {{ width: 100%; max-width: 360px; }}
td input {{ width: 100%; max-width: 150px; }}
button {{ margin-top: 12px; padding: 10px 16px; font-size: 15px; border: 0; border-radius: 6px; background: #1d5fd1; color: #fff; cursor: pointer; }}
.ok {{ color: #1c7a3d; font-weight: 600; }} .err {{ color: #b3261e; font-weight: 600; }} .muted {{ color: #5b6675; }}
</style></head><body>
<header><strong>{company} · Supplier portal</strong>
<a href="{root}">Home</a><a href="{root}/orders">Orders</a><a href="{root}/rfqs">Requests for quotation</a><a href="{root}/invoices">Invoices</a><a href="{root}/statement">Statement</a>
<a href="/web/session/logout?redirect={root}">Sign out</a></header>
<main>{body}</main></body></html>"""

esc = html.escape


def _fmt(value):
    return f'{value:,.2f}'


def _supplier():
    """The supplier this signed-in person works for, or None: only portal users linked to a registered supplier qualify."""
    user = request.env.user
    if user._is_internal() or user._is_public():
        return None
    partner = user.sudo().partner_id.commercial_partner_id
    return partner if partner.supplier_rank > 0 and partner.mos_supplier_state != 'blocked' else None


def _page(body, status=200):
    company = esc(request.env.company.sudo().name)
    return request.make_response(PAGE.format(body=body, root=ROOT, company=company), headers=[('Content-Type', 'text/html; charset=utf-8')], status=status)


def _forbidden():
    return _page('<section><h1>This page is for our suppliers</h1><p>Sign in with the supplier account the mill gave you.</p></section>', 403)


def _missing():
    return _page('<section><h1>Not found</h1><p>That document does not exist, or it is not yours.</p></section>', 404)


class SupplierPortal(http.Controller):
    # -- home ---------------------------------------------------------------------------------------------------------
    @http.route(ROOT, type='http', auth='user', website=False)
    def home(self, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        env = request.env
        orders = env['purchase.order'].sudo().search_count([('partner_id', '=', supplier.id), ('state', 'in', ('purchase', 'done'))])
        rfqs = env['mos.rfq'].sudo().search_count([('invited_ids', 'in', supplier.id), ('state', '=', 'sent')])
        subs = env['mos.supplier.submission'].sudo().search_count([('supplier_id', '=', supplier.id), ('state', '=', 'new')])
        return _page(
            f'<section><h1>{esc(supplier.name)}</h1><p class="muted">What the mill has on record for you.</p>'
            f'<table><tr><td>Orders placed with you</td><td class="n">{orders}</td></tr>'
            f'<tr><td>Requests for quotation waiting for your offer</td><td class="n">{rfqs}</td></tr>'
            f'<tr><td>Invoices you sent that are not yet entered</td><td class="n">{subs}</td></tr></table></section>'
        )

    # -- orders -------------------------------------------------------------------------------------------------------
    @http.route(f'{ROOT}/orders', type='http', auth='user')
    def orders(self, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        orders = request.env['purchase.order'].sudo().search([('partner_id', '=', supplier.id), ('state', 'in', ('purchase', 'done'))], order='date_order desc, id desc')
        rows = ''.join(
            f'<tr><td><a href="{ROOT}/orders/{o.id}">{esc(o.name)}</a></td><td>{o.date_order.date() if o.date_order else ""}</td>'
            f'<td class="n">{_fmt(o.amount_total)}</td><td class="n">{sum(max(l.product_qty - l.qty_received, 0) for l in o.order_line):,.0f}</td></tr>'
            for o in orders
        )
        return _page(f'<section><h1>Orders</h1><table><tr><th>Order</th><th>Date</th><th class="n">Amount</th><th class="n">Still to deliver</th></tr>{rows or "<tr><td colspan=4>No orders yet.</td></tr>"}</table></section>')

    def _order(self, order_id, supplier):
        order = request.env['purchase.order'].sudo().browse(order_id).exists()
        return order if order and order.partner_id == supplier and order.state in ('purchase', 'done') else None

    @http.route(f'{ROOT}/orders/<int:order_id>', type='http', auth='user', methods=['GET'])
    def order(self, order_id, saved=None, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        order = self._order(order_id, supplier)
        if not order:
            return _missing()
        rows = ''.join(
            f'<tr><td>{esc(l.product_id.display_name or l.name)}</td><td class="n">{l.product_qty:,.0f}</td><td class="n">{l.qty_received:,.0f}</td><td class="n">{_fmt(l.price_unit)}</td>'
            f'<td>{l.date_planned.date() if l.date_planned else ""}</td>'
            f'<td><input type="date" name="promise_{l.id}" value="{l.mos_promise_date or ""}"></td>'
            f'<td><input type="text" name="note_{l.id}" value="{esc(l.mos_promise_note or "")}"></td></tr>'
            for l in order.order_line
        )
        return _page(
            f'<section><h1>{esc(order.name)}</h1>{"<p class=ok>Your delivery dates were saved.</p>" if saved else ""}'
            f'<form method="post"><input type="hidden" name="csrf_token" value="{request.csrf_token()}">'
            f'<table><tr><th>Item</th><th class="n">Ordered</th><th class="n">Received</th><th class="n">Price</th><th>Wanted by</th><th>You promise</th><th>Note</th></tr>{rows}</table>'
            f'<button type="submit">Save my delivery dates</button></form></section>'
        )

    @http.route(f'{ROOT}/orders/<int:order_id>', type='http', auth='user', methods=['POST'])
    def order_promise(self, order_id, **post):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        order = self._order(order_id, supplier)
        if not order:
            return _missing()
        for line in order.order_line:
            value = (post.get(f'promise_{line.id}') or '').strip()
            note = (post.get(f'note_{line.id}') or '').strip()[:200]
            try:
                promise = fields.Date.to_date(value) if value else False
            except (ValueError, TypeError):
                return _page(f'<section><h1 class="err">"{esc(value)}" is not a date</h1><p><a href="{ROOT}/orders/{order.id}">Back</a></p></section>', 400)
            if promise and promise < fields.Date.context_today(order) - timedelta(days=1):
                return _page(f'<section><h1 class="err">A promised date cannot be in the past</h1><p><a href="{ROOT}/orders/{order.id}">Back</a></p></section>', 400)
            line.write({'mos_promise_date': promise, 'mos_promise_note': note})
        request.env['mos.audit.event'].sudo().record('supplier_promise', order, f'{supplier.name} confirmed delivery dates on {order.name}')
        return request.redirect(f'{ROOT}/orders/{order.id}?saved=1')

    # -- requests for quotation ---------------------------------------------------------------------------------------
    def _rfq(self, rfq_id, supplier):
        rfq = request.env['mos.rfq'].sudo().browse(rfq_id).exists()
        return rfq if rfq and supplier in rfq.invited_ids else None

    @http.route(f'{ROOT}/rfqs', type='http', auth='user')
    def rfqs(self, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        rfqs = request.env['mos.rfq'].sudo().search([('invited_ids', 'in', supplier.id), ('state', 'in', ('sent', 'awarded'))], order='id desc')
        rows = ''.join(f'<tr><td><a href="{ROOT}/rfqs/{r.id}">{esc(r.name)}</a></td><td>{r.deadline or ""}</td><td>{"open" if r.state == "sent" else "closed"}</td></tr>' for r in rfqs)
        return _page(f'<section><h1>Requests for quotation</h1><table><tr><th>Request</th><th>Answer by</th><th>Status</th></tr>{rows or "<tr><td colspan=3>None.</td></tr>"}</table></section>')

    @http.route(f'{ROOT}/rfqs/<int:rfq_id>', type='http', auth='user', methods=['GET'])
    def rfq(self, rfq_id, saved=None, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        rfq = self._rfq(rfq_id, supplier)
        if not rfq:
            return _missing()
        quote = rfq.quote_ids.filtered(lambda q: q.partner_id == supplier)[:1]
        prices = {l.rfq_line_id.id: l.unit_price for l in quote.line_ids}
        rows = ''.join(f'<tr><td>{esc(l.product_id.display_name)}</td><td class="n">{l.qty:,.0f}</td><td><input type="number" step="0.0001" min="0" name="price_{l.id}" value="{prices.get(l.id, "")}"></td></tr>' for l in rfq.line_ids)
        closed = rfq.state != 'sent' or (rfq.deadline and rfq.deadline < fields.Date.context_today(rfq))
        form = (
            '<p class="muted">This request is closed; you can no longer change your offer.</p>'
            if closed
            else f'<form method="post"><input type="hidden" name="csrf_token" value="{request.csrf_token()}">'
            f'<table><tr><th>Item</th><th class="n">Quantity</th><th>Your price per unit</th></tr>{rows}</table>'
            f'<label>Delivery in (days)</label><input type="number" min="0" name="lead_days" value="{quote.lead_days or ""}">'
            f'<label>Your offer is valid until</label><input type="date" name="valid_until" value="{quote.valid_until or ""}">'
            f'<label>Note</label><input type="text" name="note" value="{esc(quote.note or "")}">'
            f'<button type="submit">Send my offer</button></form>'
        )
        return _page(f'<section><h1>{esc(rfq.name)}</h1>{"<p class=ok>Your offer was received.</p>" if saved else ""}<p>Answer by {rfq.deadline or "the date given by the buyer"}.</p>{form}</section>')

    @http.route(f'{ROOT}/rfqs/<int:rfq_id>', type='http', auth='user', methods=['POST'])
    def rfq_offer(self, rfq_id, **post):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        rfq = self._rfq(rfq_id, supplier)
        if not rfq:
            return _missing()
        if rfq.state != 'sent' or (rfq.deadline and rfq.deadline < fields.Date.context_today(rfq)):
            return _page('<section><h1 class="err">This request is closed</h1></section>', 400)
        try:
            lines = []
            for line in rfq.line_ids:
                raw = (post.get(f'price_{line.id}') or '').strip()
                if raw:
                    price = float(raw)
                    if price < 0:
                        raise ValueError('negative')
                    lines.append((line, price))
            if not lines:
                raise ValueError('no price')
            lead = int(post['lead_days']) if (post.get('lead_days') or '').strip() else 0
            valid = fields.Date.to_date(post['valid_until']) if (post.get('valid_until') or '').strip() else False
        except (ValueError, TypeError):
            return _page(f'<section><h1 class="err">Please check your prices and dates</h1><p><a href="{ROOT}/rfqs/{rfq.id}">Back</a></p></section>', 400)
        env = request.env
        quote = env['mos.rfq.quote'].sudo().search([('rfq_id', '=', rfq.id), ('partner_id', '=', supplier.id)], limit=1)
        vals = {'lead_days': lead, 'valid_until': valid, 'note': (post.get('note') or '').strip()[:200]}
        if quote:
            quote.line_ids.unlink()
            quote.write(vals)
        else:
            quote = env['mos.rfq.quote'].sudo().create({'rfq_id': rfq.id, 'partner_id': supplier.id, **vals})
        for line, price in lines:
            env['mos.rfq.quote.line'].sudo().create({'quote_id': quote.id, 'rfq_line_id': line.id, 'unit_price': price})
        env['mos.audit.event'].sudo().record('supplier_offer', rfq, f'{supplier.name} answered {rfq.name}: {quote.total}')
        return request.redirect(f'{ROOT}/rfqs/{rfq.id}?saved=1')

    # -- invoices -----------------------------------------------------------------------------------------------------
    @http.route(f'{ROOT}/invoices', type='http', auth='user', methods=['GET'])
    def invoices(self, error=None, saved=None, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        env = request.env
        subs = env['mos.supplier.submission'].sudo().search([('supplier_id', '=', supplier.id)])
        rows = ''.join(
            f'<tr><td>{esc(s.supplier_ref)}</td><td>{esc(s.order_id.name or "")}</td><td class="n">{_fmt(s.amount)}</td><td>{dict(s._fields["state"].selection)[s.state]}</td><td>{esc(s.review_note or "")}</td></tr>' for s in subs
        )
        orders = env['purchase.order'].sudo().search([('partner_id', '=', supplier.id), ('state', 'in', ('purchase', 'done'))], order='id desc')
        options = '<option value="">(not for a particular order)</option>' + ''.join(f'<option value="{o.id}">{esc(o.name)}</option>' for o in orders)
        return _page(
            f'<section><h1>Invoices</h1>{"<p class=ok>Your invoice was received.</p>" if saved else ""}{f"<p class=err>{esc(error)}</p>" if error else ""}'
            f'<table><tr><th>Number</th><th>Order</th><th class="n">Amount</th><th>Status</th><th>Note</th></tr>{rows or "<tr><td colspan=5>Nothing sent yet.</td></tr>"}</table></section>'
            f'<section><h2>Send an invoice</h2><form method="post" enctype="multipart/form-data"><input type="hidden" name="csrf_token" value="{request.csrf_token()}">'
            f'<label>Your invoice number</label><input type="text" name="supplier_ref" required>'
            f'<label>Order</label><select name="order_id">{options}</select>'
            f'<label>Amount (with tax)</label><input type="number" step="0.01" min="0.01" name="amount" required>'
            f'<label>The invoice (PDF, image or spreadsheet)</label><input type="file" name="file" required>'
            f'<button type="submit">Send</button></form></section>'
        )

    @http.route(f'{ROOT}/invoices', type='http', auth='user', methods=['POST'])
    def invoice_send(self, **post):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        env = request.env
        upload = post.get('file')
        try:
            ref = (post.get('supplier_ref') or '').strip()
            amount = float(post.get('amount') or 0)
            if not ref or amount <= 0 or not upload or not upload.filename:
                raise ValidationError(env._('Give the invoice number, the amount and the file.'))
            order = False
            if (post.get('order_id') or '').strip():
                order = self._order(int(post['order_id']), supplier)
                if not order:
                    raise ValidationError(env._('That order is not yours.'))
            with env.cr.savepoint():
                sub = env['mos.supplier.submission'].sudo().create({'supplier_id': supplier.id, 'order_id': order.id if order else False, 'supplier_ref': ref[:60], 'amount': amount})
                sub.attach(upload.filename, upload.read())
            env['mos.audit.event'].sudo().record('supplier_invoice_submitted', sub, f'{supplier.name}: {ref} {amount}')
        except (UserError, ValidationError, ValueError) as error:
            message = error.args[0] if error.args and isinstance(error.args[0], str) else 'Please check the form'
            return request.redirect(f'{ROOT}/invoices?error={quote(message)}')
        except IntegrityError:
            return request.redirect(f'{ROOT}/invoices?error={quote("You have already sent an invoice with this number.")}')
        return request.redirect(f'{ROOT}/invoices?saved=1')

    # -- statement ----------------------------------------------------------------------------------------------------
    @http.route(f'{ROOT}/statement', type='http', auth='user')
    def statement(self, **_kw):
        supplier = _supplier()
        if not supplier:
            return _forbidden()
        env = request.env
        today = fields.Date.context_today(env['mos.supplier.statement'])
        statement = env['mos.supplier.statement'].sudo().create({'supplier_id': supplier.id, 'date_from': today - timedelta(days=365), 'date_to': today})
        statement.action_build()
        rows = ''.join(f'<tr><td>{l.date}</td><td>{esc(l.label or "")}</td><td class="n">{_fmt(l.amount)}</td><td class="n">{_fmt(l.balance)}</td></tr>' for l in statement.line_ids)
        return _page(
            f'<section><h1>Statement, last 12 months</h1><p>Opening balance {_fmt(statement.opening)}</p>'
            f'<table><tr><th>Date</th><th>What</th><th class="n">Amount</th><th class="n">Balance</th></tr>{rows or "<tr><td colspan=4>No movements.</td></tr>"}</table>'
            f'<p><strong>Closing balance {_fmt(statement.closing)}</strong></p></section>'
        )
