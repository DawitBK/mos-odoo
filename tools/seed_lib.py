"""Shared helpers for the lifecycle seed scripts (tools/seed_lifecycle_*.py). They are sent to the Odoo shell together, this file first:

    tools/seed_lifecycle.sh <env file>

Every step runs in a savepoint, is recorded under the parameter mos_seed.<key> when it succeeds and is skipped on the next run, so the whole
set can be run again after a failure without doubling anything. A step that fails prints why and leaves no trace.
"""

from datetime import timedelta

from odoo import Command, fields

icp = env['ir.config_parameter'].sudo()
today = fields.Date.today()
now = fields.Datetime.now()
site = env['mos.site'].search([], order='id', limit=1)
STAMP = 'seeded'  # the text an attestation records when the demo data agrees on somebody's behalf


def user(name):
    """The demo person with this role name (mos.<name>@examplecorp.com)."""
    found = env['res.users'].search([('login', '=', f'mos.{name}@examplecorp.com')], limit=1)
    if not found:
        raise RuntimeError(f'demo user mos.{name} is missing')
    return found


def in_group(xmlid, exclude=()):
    group = env.ref(xmlid)
    return env['res.users'].search([('group_ids', 'in', group.id), ('share', '=', False), ('id', 'not in', [u.id for u in exclude]), ('login', 'like', 'mos.%')], order='id', limit=1)


def product(name):
    found = env['product.product'].search([('name', '=', name)], limit=1)
    if not found:
        raise RuntimeError(f'product {name!r} is missing')
    return found


def decide(record, outcome='approve', comment='Checked against the documents.'):
    """Walk the approval request of ``record`` to its end: every pending step is decided by the first person it is waiting for."""
    request = record.mos_approval_request_id
    if not request:
        return False
    for _ in range(10):
        pending = env['mos.approval.step'].sudo().search([('request_id', '=', request.id), ('state', '=', 'pending')], order='sequence, id', limit=1)
        if not pending:
            break
        who = pending.eligible_user_ids[:1]
        if not who:
            raise RuntimeError(f'{request.display_name}: nobody can decide the pending step')
        act = {'approve': 'action_approve', 'reject': 'action_reject', 'return': 'action_return'}[outcome]
        getattr(pending.with_user(who).with_context(mos_attestation=STAMP), act)(comment)
        if outcome != 'approve':
            break
    record.invalidate_recordset()
    return True


def submit(record, who, method='action_submit'):
    """Submit as ``who``, past the "I agree" dialog the statements add when they are switched on."""
    return getattr(record.with_user(who).with_context(mos_attestation=STAMP), method)()


def confirm_po(order, buyer):
    """Take a draft purchase order through its approval chain, when the matrix asks for one, and confirm it."""
    if order._mos_approval_required():
        submit(order, buyer, 'action_mos_submit_approval')
        decide(order)
    order.with_user(buyer).button_confirm()
    return order


def pdf(tag):
    import base64

    return base64.b64encode(f'%PDF-1.4\n% {tag}\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'.encode()).decode()


def available(prod, location):
    quants = env['stock.quant'].sudo().search([('product_id', '=', prod.id), ('location_id', 'child_of', location.id), ('location_id.mos_stock_status', '=', 'available')])
    return sum(quants.mapped('quantity'))


def step(key, fn):
    if icp.get_str(f'mos_seed.{key}') == 'yes':
        print(f'skip  {key}')
        return
    try:
        with env.cr.savepoint():
            fn()
        icp.set_str(f'mos_seed.{key}', 'yes')
        env.cr.commit()
        print(f'done  {key}')
    except Exception as error:  # report it and go on: one broken step must not stop the others
        env.cr.rollback()
        env.invalidate_all()
        import traceback

        frames = [f for f in traceback.extract_tb(error.__traceback__) if 'site-packages' not in f.filename and '/odoo/' not in f.filename.replace(chr(92), '/') and 'odoo-bin' not in f.filename]
        where = f' ({frames[-1].name} line {frames[-1].lineno}: {frames[-1].line})' if frames else ''
        print(f'FAIL  {key}: {type(error).__name__}: {str(error)[:400]}{where}')
