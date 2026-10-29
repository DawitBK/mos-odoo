"""Commercial demo data for MOS: approved customers and price lists, price floors, credit limit requests, credit notes, returns
and proofs of delivery, then the commercial controls are switched on. Run inside the Odoo shell after the other seed scripts.
"""

from datetime import timedelta

from odoo import Command, fields

if env['mos.price.floor'].search_count([]):
    print('Commercial demo data is already there; nothing to do.')
    raise SystemExit(0)

today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
salesmgr = user('mos.salesmgr@examplecorp.com')
salesrep = user('mos.salesrep@examplecorp.com')
finance = user('mos.finance@examplecorp.com')
store = user('mos.storekeeper@examplecorp.com')
labmgr = user('mos.labmanager@examplecorp.com')
site = env['mos.site'].search([], limit=1)

# --- customers and price lists ---------------------------------------------------------------------------------------------
customers = env['sale.order'].search([]).partner_id.commercial_partner_id
for customer in customers:
    customer.with_user(salesmgr).action_approve_customer()
for pricelist in env['product.pricelist'].search([]):
    pricelist.sudo().write({'mos_state': 'approved', 'mos_approved_by_id': salesmgr.id})

# --- price floors: never below cost plus a margin -----------------------------------------------------------------------------
for product in env['product.product'].search([('list_price', '>', 0), ('standard_price', '>', 0)]):
    if product.categ_id.name in ('Flour', 'Packed flour') or 'flour' in product.name.lower() or 'semolina' in product.name.lower():
        env['mos.price.floor'].create({'product_id': product.id, 'floor_price': round(product.standard_price * 1.12, 4)})
env.cr.commit()

# --- credit limit requests ----------------------------------------------------------------------------------------------------
if customers:
    first, second = customers[0], customers[-1]
    first.with_context(mos_credit_change=True).write({'mos_credit_limit': max(first.mos_credit_limit, 56000000.0)})
    applied = env['mos.credit.change'].with_user(salesmgr).create({'partner_id': first.id, 'new_limit': 91000000.0, 'reason': 'Twelve months of on-time payment, volume doubled'})
    applied.with_user(finance).action_approve()
    env['mos.credit.change'].with_user(salesmgr).create({'partner_id': second.id, 'new_limit': 126000000.0, 'reason': 'New contract with a hotel chain'})

# --- credit notes ----------------------------------------------------------------------------------------------------------------
reasons = {}
for code, name in (('SHORT', 'Shortage on delivery'), ('DMG', 'Damaged bags'), ('PRICE', 'Price correction')):
    reasons[code] = env['mos.reason'].create({'domain': 'credit', 'code': code, 'name': name})
return_reason = env['mos.reason'].create({'domain': 'customer_return', 'code': 'RET-DMG', 'name': 'Damaged in transit'})
env['mos.approval.rule'].create({'doc_type': 'mos.credit.note', 'sequence': 10, 'amount_from': 700000, 'group_id': env.ref('mos_approvals.group_mos_finance_manager').id, 'sla_hours': 24})
invoices = env['mos.customer.invoice'].search([('balance', '>', 2100000)], order='id desc', limit=4)
for invoice, code, amount, finish in zip(invoices, ('SHORT', 'PRICE', 'DMG', 'SHORT'), (252000.0, 448000.0, 1330000.0, 1680000.0), (True, True, False, None)):
    note = env['mos.credit.note'].with_user(salesrep).create({'customer_id': invoice.customer_id.id, 'invoice_id': invoice.id, 'reason_id': reasons[code].id, 'amount': amount, 'note': 'Raised from the delivery documents'})
    if finish is None:
        continue
    note.with_user(salesrep).action_submit()
    if finish:
        if note.state == 'pending':
            note.mos_approval_request_id.current_step_id.with_user(finance).action_approve('Checked against the weighbridge ticket')
        note.with_user(finance).action_post()
env.cr.commit()

# --- returns ------------------------------------------------------------------------------------------------------------------------
dispatched = env['mos.delivery.order'].search([('state', '=', 'dispatched')], order='id desc', limit=3)
for number, delivery in enumerate(dispatched):
    product = delivery.invoice_id.product_id
    if not product:
        continue
    ret = env['mos.customer.return'].with_user(salesrep).create({'site_id': site.id, 'customer_id': delivery.partner_id.id, 'delivery_id': delivery.id, 'product_id': product.id, 'qty': 300.0 + 100 * number, 'reason_id': return_reason.id, 'note': 'Bags torn on arrival'})
    ret.with_user(store).action_receive()
    if number == 0:
        ret.with_user(labmgr).action_decide('restock', 'Flour dry, bags re-sewn')
        ret.with_user(salesrep).action_credit()
env.cr.commit()

# --- proofs of delivery -------------------------------------------------------------------------------------------------------------
signature = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
for number, delivery in enumerate(env['mos.delivery.order'].search([('state', '=', 'dispatched')], order='id', limit=6)):
    delivery.action_pod_link()
    if number % 3 != 2:
        delivery.record_pod(('Hana Tesfaye', 'Dawit Bekele', 'Selam Girma', 'Yonas Alemu')[number % 4], signature, 'Received in good order' if number % 2 == 0 else '')

# --- from now on the commercial controls apply -----------------------------------------------------------------------------------
icp = env['ir.config_parameter'].sudo()
icp.set_str('mos_commercial.enforce_customer_approval', 'True')
icp.set_str('mos_commercial.enforce_pricing', 'True')
icp.set_str('mos_commercial.control_credit_changes', 'True')
env['mos.exception']._cron_scan()
env.cr.commit()
print('commercial ready:', env['mos.credit.note'].search_count([]), 'credit notes,', env['mos.customer.return'].search_count([]), 'returns')
