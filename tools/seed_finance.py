"""Finance demo data for MOS: account mapping, a self-billing supplier, a proposed payment run and a journal export of the older
postings. Run inside the Odoo shell after the other seed scripts. Skips itself when it has run before.
"""

from datetime import timedelta

from odoo import fields

if env['mos.account.map'].search_count([]):
    print('Finance demo data is already there; nothing to do.')
    raise SystemExit(0)

today = fields.Date.today()
user = lambda login: env['res.users'].search([('login', '=', login)], limit=1)
finance = user('mos.finance@examplecorp.com')
cashier = user('mos.cashier@examplecorp.com')

for key, code, name in (
    ('inventory', '1300', 'Raw materials and stock'),
    ('input_vat', '1410', 'Input VAT receivable'),
    ('ap_control', '2100', 'Accounts payable'),
    ('wht_payable', '2250', 'Withholding tax payable'),
    ('bank', '1020', 'Bank accounts'),
    ('cash', '1010', 'Cash on hand'),
    ('ar_control', '1200', 'Accounts receivable'),
    ('sales', '4000', 'Sales of flour and by-products'),
    ('output_vat', '2310', 'Output VAT payable'),
):
    env['mos.account.map'].create({'key': key, 'code': code, 'name': name})

env.cr.commit()

# a supplier that issues no invoices: from now on its receipts are self-billed
farmer = env['res.partner'].search([('supplier_rank', '>', 0)], order='id desc', limit=1)
if farmer:
    farmer.mos_self_billing = True

env.cr.commit()

# older postings go to the accountants; the recent ones are still open
try:
    batch = env['mos.journal.batch'].with_user(finance).export(today - timedelta(days=90), today - timedelta(days=10))
    print('journal exported:', batch.name)
except Exception as error:
    env.cr.rollback()
    print('journal export skipped:', error)

account = env['mos.paying.account'].search([], limit=1)
if account and cashier:
    run = env['mos.payment.run'].with_user(cashier).create({'account_id': account.id, 'method': 'transfer'})
    try:
        run.action_propose()
    except Exception:
        run.unlink()
env.cr.commit()
print('finance ready:', env['mos.account.map'].search_count([]), 'accounts mapped,', env['mos.payment.run'].search_count([]), 'payment run proposed')
