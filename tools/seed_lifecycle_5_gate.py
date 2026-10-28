"""Lifecycle seed, part 5: the gate beyond wheat and flour. A contractor with tools coming and going, an auditor, a truck carrying two orders
weighed in steps, a transfer truck, a cancelled arrival."""

guard, gatesup, admin = user('guard'), user('gatesup'), user('admin')
Visit = env['mos.gate.visit']


def arrive(visit):
    """A walk-in is registered as arrived; a visit announced beforehand is still expected until the truck reaches the gate."""
    if visit.state == 'expected':
        visit.with_user(guard).action_arrive()


def service_visits():
    inside = Visit.with_user(guard).create(
        {
            'site_id': site.id,
            'purpose': 'service',
            'plate': 'AA-77120',
            'driver_name': 'Samson Girma',
            'transporter': 'Zenith Electrical Works',
            'tool_ids': [Command.create({'name': 'Cable drum', 'qty_in': 2}), Command.create({'name': 'Ladder', 'qty_in': 1}), Command.create({'name': 'Welding set', 'qty_in': 1})],
        }
    )
    arrive(inside)  # still on site: the tools have not left
    gone = Visit.with_user(guard).create(
        {
            'site_id': site.id,
            'purpose': 'service',
            'plate': 'OR-55318',
            'driver_name': 'Mulu Alemu',
            'transporter': 'Rift Lubricants and Spares',
            'tool_ids': [Command.create({'name': 'Grease gun', 'qty_in': 2, 'qty_out': 2}), Command.create({'name': 'Drum of gear oil', 'qty_in': 3, 'qty_out': 2, 'explanation': 'One drum was used up in the gearbox service'})],
        }
    )
    arrive(gone)
    gone.with_user(guard).action_clear_exit()
    gone.with_user(guard).action_exit()
    other = Visit.with_user(guard).create({'site_id': site.id, 'purpose': 'other', 'plate': 'AM-20817', 'driver_name': 'Rahel Tadesse', 'transporter': 'External auditor (food safety)'})
    arrive(other)  # a visitor who is still inside (every other purpose needs a ticket before the exit is cleared)


def transfer_trucks():
    trucks = (('TG-90412', 'Yared Bekele', 'outbound_transfer'), ('AA-31566', 'Selam Haile', 'inbound_transfer'))
    for plate, driver, purpose in trucks:
        visit = Visit.with_user(guard).create({'site_id': site.id, 'purpose': purpose, 'plate': plate, 'driver_name': driver, 'transporter': 'Own fleet'})
        if purpose == 'outbound_transfer':
            arrive(visit)
    dropped = Visit.with_user(guard).create({'site_id': site.id, 'purpose': 'inbound_po', 'plate': 'AA-60001', 'driver_name': 'Lemma Desta', 'transporter': 'Rift Valley Grain Traders'})
    dropped.with_user(gatesup).action_cancel()


def two_orders_one_truck():
    for visit in Visit.search([('purpose', '=', 'inbound_po'), ('state', '=', 'expected'), ('purchase_id', '!=', False)], order='id desc'):
        line = visit.purchase_id.order_line.filtered(lambda l: l.product_id.type == 'consu')[:1]
        other = env['purchase.order'].search([('state', '=', 'purchase'), ('receipt_status', '!=', 'full'), ('id', '!=', visit.purchase_id.id), ('order_line.product_id', '=', line.product_id.id)], limit=1)
        if other:
            break
    else:
        raise RuntimeError('no expected delivery with a second open order for the same item')
    env['mos.gate.visit.share'].with_user(guard).create({'visit_id': visit.id, 'purchase_id': other.id, 'product_id': line.product_id.id})
    env['mos.gate.weigh.step'].with_user(guard).create({'visit_id': visit.id, 'weight_kg': 52400.0, 'note': 'Loaded, before unloading anything'})
    env['mos.gate.weigh.step'].with_user(guard).create({'visit_id': visit.id, 'sequence': 20, 'weight_kg': 44600.0, 'note': 'After the first order came off'})
    visit.with_user(guard).action_allocate_from_steps()


step('service_visits', service_visits)
step('transfer_trucks', transfer_trucks)
step('two_orders_one_truck', two_orders_one_truck)
