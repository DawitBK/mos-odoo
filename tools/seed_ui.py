"""Demo data for the screens drawn in the client's documents. Run inside the Odoo shell after the other seed scripts.

Switches on the client's rules (the "I agree" statements, the deposit slip), gives the requests their priority and remarks, the
inbound deliveries their delivery note and items, and the invoices and vouchers their attached documents (one of them verified).
Skips itself when it has run before.
"""

import base64

from datetime import timedelta

from odoo import Command, fields

icp = env['ir.config_parameter'].sudo()
if all(icp.get_str(key) == 'yes' for key in ('mos_ui.seeded', 'mos_ui.requests_seeded', 'mos_ui.counts_seeded')):
    print('Screen demo data is already there; nothing to do.')
    raise SystemExit(0)

user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
clerk, finance, buyer, guard = user('mos.apclerk@examplecorp.com'), user('mos.finance@examplecorp.com'), user('mos.buyer@examplecorp.com'), user('mos.guard@examplecorp.com')
today = fields.Date.today()


def pdf(tag):
    return base64.b64encode(f'%PDF-1.4\n% {tag}\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'.encode()).decode()


made = attached = 0
if icp.get_str('mos_ui.seeded') != 'yes':
    # --- requests: priority, remarks, employee id ------------------------------------------------------------------------
    for number, request in enumerate(env['mos.purchase.request'].search([], order='id'), 1):
        request.write(
            {
                'priority': ['medium', 'high', 'low', 'urgent'][number % 4],
                'remarks': 'Raised for the weekly milling plan.' if number % 2 else False,
                'requester_ref': f'EMP-{500 + number}',
                'department': request.department or 'Milling',
            }
        )

    # --- deliveries: delivery note, items taken from the order ---------------------------------------------------------------
    made = 0
    for visit in env['mos.gate.visit'].search([('purpose', '=', 'inbound_po'), ('purchase_id', '!=', False)], order='id'):
        if visit.delivery_line_ids:
            continue
        for order_line in visit.purchase_id.order_line.filtered(lambda l: l.product_id.type == 'consu')[:1]:
            declared = visit.declared_qty or visit.net_weight or order_line.product_qty
            if declared <= 0:
                continue
            env['mos.gate.visit.line'].create({'visit_id': visit.id, 'product_id': order_line.product_id.id, 'declared_qty': declared, 'uom_id': order_line.product_id.uom_id.id, 'bags': 0})
        if not visit.delivery_note:
            visit.delivery_note = f'DN-{today.year}-{visit.id:04d}'
        if visit.state not in ('arrived', 'cancelled'):
            visit.entry_state = 'approved'
        made += 1

    # --- attached documents, one of them verified ---------------------------------------------------------------------------
    attached = 0
    for number, invoice in enumerate(env['mos.supplier.invoice'].search([], order='id', limit=4)):
        doc = env['mos.attachment'].with_user(clerk).create(
            {'name': f'invoice-{invoice.name}.pdf', 'file': pdf(f'invoice {invoice.name}'), 'res_model': invoice._name, 'res_id': invoice.id, 'kind': 'supplier_invoice', 'note': f'Supplier invoice {invoice.supplier_ref}'}
        )
        attached += 1
        if number % 2 == 0:
            doc.with_user(finance).action_verify()
        if number == 0:
            env['mos.attachment'].with_user(clerk).create(
                {'name': f'tax-{invoice.name}.pdf', 'file': pdf(f'tax {invoice.name}'), 'res_model': invoice._name, 'res_id': invoice.id, 'kind': 'tax_document', 'note': 'VAT invoice from the tax authority'}
            )
            attached += 1
    for voucher in env['mos.payment.voucher'].search([('state', 'in', ('approved', 'partially_paid'))], order='id'):
        if voucher.mos_attachment_ids.filtered(lambda a: a.kind == 'deposit_slip'):
            continue
        env['mos.attachment'].with_user(clerk).create(
            {'name': f'deposit-slip-{voucher.name}.pdf', 'file': pdf(f'slip {voucher.name}'), 'res_model': voucher._name, 'res_id': voucher.id, 'kind': 'deposit_slip', 'note': 'Bank deposit slip'}
        )
        attached += 1

# --- the client's rules ----------------------------------------------------------------------------------------------
if icp.get_str('mos_ui.seeded') != 'yes':
    icp.set_str('mos_approvals.require_attestation', 'True')
    icp.set_str('mos_documents.require_deposit_slip', 'True')
    icp.set_str('mos_ui.seeded', 'yes')

# --- requests for things that are not in stock, so ordering from a request can be shown end to end -----------------
if icp.get_str('mos_ui.requests_seeded') != 'yes' and not env['mos.purchase.request.line'].search_count([('product_id.name', '=', 'Polypropylene flour bags')]):
    kg = env.ref('uom.product_uom_kgm')
    site = env['mos.site'].search([], limit=1)
    for number, (name, qty, price, reason) in enumerate(
        (('Polypropylene flour bags', 20000, 18.0, 'Flour bags for the packing line'), ('Roller mill lubricant', 120, 450.0, 'Lubricant for the roller mills'), ('Plansifter sieve cloth', 12, 2600.0, 'Sieve cloth for the plansifter'))
    ):
        product = env['product.product'].search([('name', '=', name)], limit=1) or env['product.product'].create(
            {'name': name, 'type': 'consu', 'is_storable': True, 'purchase_ok': True, 'sale_ok': False, 'uom_id': kg.id, 'standard_price': price}
        )
        request = env['mos.purchase.request'].with_user(buyer).create(
            {
                'site_id': site.id,
                'department': 'Operations',
                'reason': reason,
                'priority': ['high', 'medium', 'low'][number],
                'requester_ref': 'EMP-503',
                'needed_by': today + timedelta(days=14),
                'line_ids': [Command.create({'product_id': product.id, 'qty': qty, 'estimated_price': price, 'specification': 'https://example.com/specs/' + name.lower().replace(' ', '-')})],
            }
        )
        if number < 2:
            request.with_user(buyer).action_submit()
icp.set_str('mos_ui.requests_seeded', 'yes')

# --- counts: blind by default, a recount limit and a review, with one sheet waiting in review -----------------------
if icp.get_str('mos_ui.counts_seeded') != 'yes':
    icp.set_str('mos_counts.recount_pct', '2')
    icp.set_str('mos_counts.require_review', 'True')
    storekeeper = user('mos.storekeeper@examplecorp.com')
    location = env['stock.quant'].search([('location_id.usage', '=', 'internal'), ('quantity', '>', 0), ('location_id.name', 'like', 'Silo')], limit=1).location_id
    site = env['mos.site'].search([], limit=1)
    if location and storekeeper:
        count = env['mos.cycle.count'].with_user(storekeeper).create({'site_id': site.id, 'location_id': location.id})
        count.action_start()
        for line in count.line_ids:
            line.with_user(storekeeper).write({'counted_qty': round(line.system_qty * 0.999, 1)})
        count.with_user(storekeeper).action_send_review()
    icp.set_str('mos_ui.counts_seeded', 'yes')
env.cr.commit()
print(f'Screen demo data loaded: {made} deliveries with items, {attached} documents, statements and deposit slips switched on.')
