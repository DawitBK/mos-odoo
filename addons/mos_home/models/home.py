from odoo import api, fields, models
from odoo.exceptions import AccessError

# Quick actions: (action xml id, label, icon, tone, opens a new record). A tile shows only for a person who may do what it says.
TILES = (
    ('mos_procurement.action_mos_purchase_request', 'New purchase request', 'cart', 'blue', True),
    ('mos_gate_plus.action_mos_gate_arrival', 'Register an arrival', 'truck', 'amber', False),
    ('mos_inbound.action_mos_goods_receiving', 'Goods receiving', 'arrow-in', 'green', False),
    ('mos_quality.action_mos_lab_queue', 'Lab queue', 'flask', 'purple', False),
    ('mos_payables.action_mos_supplier_invoice', 'New supplier invoice', 'receipt', 'amber', True),
    ('mos_payables.action_mos_payment_voucher', 'Payment requests', 'money', 'blue', False),
    ('mos_inventory.action_mos_cycle_count', 'New physical count', 'clipboard', 'green', True),
    ('mos_stock_plus.action_mos_goods_transfer', 'New goods transfer', 'transfer', 'purple', True),
    ('mos_production.action_mos_milling_run', 'Milling runs', 'factory', 'blue', False),
    ('mos_sales.action_mos_sale_order', 'Sales orders', 'users', 'green', False),
    ('mos_approvals.action_mos_approval_inbox', 'Approval inbox', 'check', 'red', False),
    ('mos_process_map.action_mos_process_map', 'Process map', 'transfer', 'neutral', False),
)
MAX_TILES = 8
OPEN_VISIT_STATES = ('arrived', 'weighing', 'weighed', 'cleared')


class MosOverview(models.TransientModel):
    _inherit = 'mos.overview'

    @api.model
    def action_open(self):
        return {'type': 'ir.actions.client', 'tag': 'mos_home.home', 'name': self.env._('Overview')}

    @api.model
    def home_action(self, xmlid, new=False):
        """The action a quick-action tile opens. Only the tiles of the home screen can be asked for."""
        if xmlid not in {tile[0] for tile in TILES}:
            raise AccessError(self.env._('This is not a quick action of the home screen.'))
        action = self.env['ir.actions.actions']._for_xml_id(xmlid)
        if new and action.get('type') == 'ir.actions.act_window' and action.get('target') != 'new':
            action = dict(action, views=[(False, 'form')], view_mode='form', res_id=False, target='current')
        return action

    @api.model
    def get_home(self):
        """Everything the home screen shows, for the person who opens it and the operation unit they work at. A part the person may not see is left out, never an error."""
        env = self.env
        overview = self.create({})
        site = env.user._mos_current_site()

        def guarded(part):
            try:
                return part()
            except AccessError:
                return []

        def card(label, field, method=None, xmlid=None, tone='blue', icon='doc', unit='', digits=0):
            return {'label': label, 'value': round(overview[field], digits) if digits else int(overview[field] or 0), 'unit': unit, 'method': method, 'xmlid': xmlid, 'tone': tone, 'icon': icon}

        today_cards = [
            card(env._('Trucks on site'), 'trucks_on_site', 'action_open_trucks', tone='blue', icon='truck'),
            card(env._('Waiting for a lab decision'), 'inspections_open', 'action_open_inspections', tone='amber', icon='flask'),
            card(env._('Wheat received today'), 'received_today_kg', tone='green', icon='arrow-in', unit='kg'),
            card(env._('Silo capacity used'), 'silo_fill_pct', 'action_open_silos', tone='purple', icon='sack', unit='%', digits=1),
        ]
        month_cards = [
            card(env._('Wheat received'), 'received_month_kg', tone='green', unit='kg'),
            card(env._('Rejection rate'), 'rejection_rate', tone='red', unit='%', digits=1),
            card(env._('Extraction, last 30 days'), 'extraction_30d', tone='purple', unit='%', digits=2),
            card(env._('Invoiced to customers (with VAT)'), 'sales_month_amount', tone='blue'),
            card(env._('We owe suppliers'), 'payable_total', tone='amber'),
            card(env._('Customers owe us'), 'receivable_total', tone='amber'),
        ]
        attention = [
            card(env._('Approvals waiting for me'), 'approvals_waiting', 'action_open_approvals', tone='amber', icon='check'),
            card(env._('Exceptions assigned to me'), 'exceptions_mine', 'action_open_exceptions', tone='red', icon='alert'),
            card(env._('Deliveries overdue'), 'orders_late', 'action_open_late_deliveries', tone='red', icon='clock'),
            card(env._('Vouchers ready to pay'), 'vouchers_to_pay', 'action_open_vouchers', tone='amber', icon='money'),
            card(env._('Waiting in the lab'), 'lab_waiting', 'action_open_lab_queue', tone='blue', icon='flask'),
            card(env._('Purchase requests waiting'), 'requests_open', xmlid='mos_procurement.action_mos_purchase_request', tone='blue', icon='cart'),
            card(env._('Stock in quarantine'), 'quarantine_kg', tone='purple', icon='lock', unit='kg'),
        ]

        tiles = []
        for xmlid, label, icon, tone, new in TILES:
            action = env(su=True).ref(xmlid, raise_if_not_found=False)  # a person may open an action without being allowed to read its record
            model = getattr(action, 'res_model', False) if action else False
            if not model or model not in env:
                continue
            if not env[model].has_access('create' if new and not env[model]._transient else 'read'):
                continue
            tiles.append({'xmlid': xmlid, 'label': env._(label), 'icon': icon, 'tone': tone, 'new': new})
        tiles = tiles[:MAX_TILES]

        def silos():
            rows = env['mos.silo'].search([('site_id', '=', site.id)] if site else []).fresh()
            return [
                {'id': s.id, 'code': s.code, 'name': s.name, 'grain': s.product_id.name or '', 'level': round(s.level_kg), 'capacity': round(s.capacity_kg), 'pct': round(s.fill_pct, 1), 'status': s.status}
                for s in rows
            ]

        def gate():
            visit = env['mos.gate.visit']
            labels = dict(visit._fields['state'].selection)
            rows = visit.search([('state', 'in', OPEN_VISIT_STATES)] + ([('site_id', '=', site.id)] if site else []), limit=6, order='arrived_at desc')
            return [
                {'id': v.id, 'name': v.name, 'plate': v.plate, 'who': v.partner_id.name or v.transporter or '', 'state': v.state, 'state_label': labels.get(v.state, v.state), 'at': fields.Datetime.to_string(v.arrived_at)}
                for v in rows
            ]

        def runs():
            run = env['mos.milling.run']
            labels = dict(run._fields['state'].selection)
            return [
                {'id': r.id, 'name': r.name, 'date': fields.Date.to_string(r.run_date), 'wheat': round(r.wheat_kg), 'flour': round(r.flour_kg), 'extraction': round(r.extraction_pct, 1), 'state': r.state, 'state_label': labels.get(r.state, r.state)}
                for r in overview.run_ids
            ][:5]

        silo_rows = guarded(silos)
        mix = {}
        for row in silo_rows:
            mix[row['grain'] or env._('Unassigned')] = mix.get(row['grain'] or env._('Unassigned'), 0) + row['level']
        grain_mix = [{'label': k, 'value': v} for k, v in sorted(mix.items(), key=lambda kv: -kv[1]) if v > 0]

        return {
            'overview_id': overview.id,
            'user': env.user.name,
            'site': site.name if site else env.company.name,
            'as_of': self.env._('operations as of %s', fields.Datetime.context_timestamp(overview, fields.Datetime.now()).strftime('%d %b %H:%M')),
            'today': today_cards,
            'month': month_cards,
            'attention': attention,
            'tiles': tiles,
            'silos': silo_rows,
            'grain_mix': grain_mix,
            'gate': guarded(gate),
            'runs': guarded(runs),
        }
