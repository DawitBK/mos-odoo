"""Tops up the confirmed purchase orders for local wheat that nothing has been received against, so tools/ui-journey.mjs can be run again
(each run receives one whole order). Run inside the Odoo shell. Prints how many it added.

    docker compose --env-file <env> exec -T odoo odoo shell -d mos --db_host db --db_user odoo --db_password <pw> --no-http < tools/seed_journey_orders.py
"""

import random

from odoo import Command

WANTED = 5

buyer = env['res.users'].search([('login', '=', 'mos.buyer@examplecorp.com')], limit=1)
products = env['product.product'].search([('name', 'ilike', '(local)'), ('type', '=', 'consu')])
suppliers = env['res.partner'].search([('supplier_rank', '>', 0), ('mos_supplier_state', '=', 'approved')])
if not (buyer and products and suppliers):
    print('Nothing to add: the demo data has no buyer, local wheat or approved supplier.')
    raise SystemExit(0)

open_orders = env['purchase.order'].sudo().search_count(
    [('state', '=', 'purchase'), ('receipt_status', '!=', 'full'), ('order_line.product_id', 'in', products.ids)]
)
added = 0


def history(supplier, product):
    return env['mos.lab.inspection'].sudo().search_count([('product_id', '=', product.id), ('visit_id.partner_id', '=', supplier.id), ('state', '=', 'decided')])


for number in range(max(WANTED - open_orders, 0)):
    product = products[number % len(products)]
    # the quality policy waives the inspection of a supplier with a long clean run (skip-lot): order from the one with the shortest history,
    # so the journey always has a delivery that is inspected
    supplier = min(suppliers, key=lambda s: (history(s, product), s.id))
    order = env['purchase.order'].with_user(buyer).create(
        {
            'partner_id': supplier.id,
            'order_line': [Command.create({'product_id': product.id, 'product_qty': random.choice((30000, 32000, 34000, 36000, 38000)), 'price_unit': product.standard_price})],
        }
    )
    if order._mos_approval_required():
        order.with_context(mos_attestation='seeded').action_mos_submit_approval()
        while True:
            step = env['mos.approval.step'].sudo().search([('request_id', '=', order.mos_approval_request_id.id), ('state', '=', 'pending')], limit=1)
            if not step:
                break
            approver = step.eligible_user_ids[:1]
            step.with_user(approver).with_context(mos_attestation='seeded').action_approve('seeded for the browser journey')
    order.with_user(buyer).button_confirm()
    added += 1
env.cr.commit()
print(f'{added} order(s) for local wheat added; {open_orders + added} are open.')
