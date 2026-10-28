"""Lifecycle seed, part 3: quality and production. Deduction bands, inspections at every stage, concessions in every state, decision
revisions, certificates of analysis, a lost retention sample, work orders in every state with labour, planning suggestions."""

analyst, labmgr, gm, admin, production, store = user('analyst'), user('labmanager'), user('gm'), user('admin'), user('production'), user('storekeeper')
Inspection = env['mos.lab.inspection']


def deduction_bands():
    labels = {'Moisture': ((13.51, 14.5, 1.0, 0.0), (14.51, 16.0, 3.0, -2.0)), 'Protein': ((9.0, 10.99, 0.0, -1.5),)}
    for spec in env['mos.lab.spec'].search([('state', '=', 'approved'), ('product_id.name', 'like', 'wheat')]):
        draft = spec.action_new_version()
        for line in draft.line_ids:
            for key, bands in labels.items():
                if line.name.lower().startswith(key.lower()):
                    for low, high, weight, price in bands:
                        env['mos.lab.deduction.rule'].create({'line_id': line.id, 'from_value': low, 'to_value': high, 'weight_deduction_pct': weight, 'price_adjust_pct': price})
        draft.with_user(labmgr).action_approve()


def _fill(insp, failing=(), who=None):
    """Enter a result for every parameter: inside the limits, except for the parameters named in ``failing`` (just outside)."""
    who = who or analyst
    for line in insp.spec_id.line_ids:
        middle = (line.min_value + line.max_value) / 2.0
        value = line.max_value * 1.08 if any(line.name.lower().startswith(f.lower()) for f in failing) else middle
        insp.with_user(who).record_result(line, round(value, 3))
    if insp.samples_taken < insp.samples_required:
        insp.sudo().write({'samples_taken': insp.samples_required})


def inspections():
    open_ones = Inspection.search([('state', '=', 'open')], order='id desc')
    if len(open_ones) < 5:
        raise RuntimeError('not enough open inspections')
    icp.set_str('mos_master_plus.concession_approval', 'True')
    ready, minor, major, rejected, approved = open_ones[:5]
    # a delivery that passed everything and waits for its decision
    _fill(ready)
    ready.with_user(analyst).action_submit_results()
    # a failed result accepted under concession: waits for the general manager
    first = minor.spec_id.line_ids[0].name
    _fill(minor, failing=(first,))
    minor.with_user(analyst).action_submit_results()
    minor.with_user(labmgr).decide('accept', 0.0, 'Supplier is under contract and the next three loads will be tested on arrival')
    # two failures with a smaller deduction: the general manager refuses, and the lab manager rejects the delivery
    names = ['Moisture'] + [line.name for line in major.spec_id.line_ids if not line.name.lower().startswith('moisture')][:1]
    _fill(major, failing=tuple(names))
    major.with_user(analyst).action_submit_results()
    major.with_user(labmgr).decide('accept', 0.0, 'The supplier asked for leniency because of the weather')
    if not major.concession_ids:
        raise RuntimeError('accepting two failed parameters did not raise a concession')
    decide(major.concession_ids[:1], 'reject', 'Two parameters failed: not a concession we can give.')
    major.with_user(labmgr).decide('reject', 0.0, 'Failed moisture and gluten; the supplier was told')
    # a failed result accepted and approved: the decision is applied
    _fill(rejected, failing=(rejected.spec_id.line_ids[0].name,))
    rejected.with_user(analyst).action_submit_results()
    rejected.with_user(labmgr).decide('accept', 0.0, 'Retested at the supplier\'s expense: within the contract tolerance')
    decide(rejected.concession_ids[:1])
    # a concession the lab manager withdrew
    _fill(approved, failing=(approved.spec_id.line_ids[0].name,))
    approved.with_user(analyst).action_submit_results()
    approved.with_user(labmgr).decide('accept', 0.0, 'Raised too early, before the retest')
    approved.concession_ids[:1].with_user(labmgr).action_cancel()


def revisions():
    decided = Inspection.search([('state', '=', 'decided'), ('decision', '=', 'accept'), ('visit_id.purchase_id', '!=', False)], order='id desc', limit=12)
    mine = decided.filtered(lambda i: i.visit_id.purchase_id.invoice_ids if 'invoice_ids' in i.visit_id.purchase_id._fields else True)[:3] or decided[:3]
    if len(mine) < 3:
        raise RuntimeError('not enough decided inspections to revise')
    waiting, applied, refused = mine
    Revision = env['mos.lab.revision']
    Revision.with_user(labmgr).create({'inspection_id': waiting.id, 'new_decision': 'accept_deduction', 'new_deduction_pct': 2.0, 'note': 'Third-party laboratory found moisture above the limit'})
    done = Revision.with_user(labmgr).create({'inspection_id': applied.id, 'new_decision': 'accept_deduction', 'new_deduction_pct': 1.5, 'note': 'Result entered against the wrong lot: corrected'})
    done.with_user(admin).action_approve()
    no = Revision.with_user(labmgr).create({'inspection_id': refused.id, 'new_decision': 'reject', 'note': 'Customer complaint, no sample kept'})
    no.with_user(admin).action_reject()


def certificates():
    candidates = Inspection.search([('state', '=', 'decided'), ('decision', 'in', ('accept', 'accept_deduction'))], order='id desc', limit=5)
    made = env['mos.lab.coa']
    for insp in candidates:
        made |= env['mos.lab.coa'].with_user(labmgr).issue(insp, insp.visit_id)
    made[-1:].with_user(labmgr).action_revoke('Issued against the wrong delivery')


def lost_sample():
    sample = env['mos.lab.sample'].search([('state', '=', 'retained')], order='id desc', limit=1)
    if not sample:
        raise RuntimeError('no retained sample to lose')
    sample.with_user(analyst).action_mark_lost('Bag torn in the sample store, label unreadable')


def work_orders():
    recipes = env['mos.recipe'].search([('state', '=', 'approved')])
    centre = env['mos.work.centre'].search([('kind', '=', 'milling')], limit=1)
    if not recipes:
        raise RuntimeError('no approved recipe')

    def make(recipe, kg, days, shift='day'):
        return env['mos.work.order'].with_user(production).create({'site_id': site.id, 'recipe_id': recipe.id, 'planned_kg': kg, 'date': today + timedelta(days=days), 'shift': shift, 'work_centre_id': centre.id, 'kind': 'campaign'})

    make(recipes[0], 20000, 3)  # draft
    planned = make(recipes[-1], 20000, 1, 'night')
    planned.with_user(production).action_plan()
    running = make(recipes[0], 20000, 0)
    running.with_user(production).action_plan()
    running.with_user(production).action_start()
    finished = make(recipes[0], 20000, -1)
    finished.with_user(production).action_plan()
    finished.with_user(production).action_start()
    run = finished.run_id
    flour, bran = recipes[0].product_id, product('Wheat bran')
    premix = flour.product_tmpl_id.mos_premix_id
    dose = round(14.8 * flour.product_tmpl_id.mos_premix_g_per_t / 1000.0, 2)
    run.write(
        {
            'output_ids': [Command.create({'product_id': flour.id, 'kind': 'flour', 'qty_kg': 14800}), Command.create({'product_id': bran.id, 'kind': 'bran', 'qty_kg': 4600})],
            'dosing_ids': [Command.create({'premix_id': premix.id, 'actual_kg': dose})] if premix else [],
        }
    )
    run.with_user(production).action_complete()
    finished.with_user(production).action_finish()
    dropped = make(recipes[0], 15000, 2)
    dropped.with_user(production).action_cancel()
    for order, rows in ((running, (('Tesfaye M.', 8.0), ('Almaz K.', 8.0), ('Bekele D.', 6.5))), (finished, (('Tesfaye M.', 8.0), ('Almaz K.', 7.5), ('Hana G.', 8.0), ('Yonas T.', 4.0)))):
        for worker, hours in rows:
            env['mos.labour.log'].with_user(production).create({'order_id': order.id, 'worker': worker, 'hours': hours, 'date': order.date})


step('deduction_bands', deduction_bands)
step('inspections', inspections)
step('revisions', revisions)
step('certificates', certificates)
step('lost_sample', lost_sample)
step('work_orders', work_orders)
