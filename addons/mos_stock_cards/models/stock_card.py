from datetime import datetime, time, timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

KINDS = [
    ('receipt', 'Receipt'),
    ('issue', 'Issue'),
    ('transfer_in', 'Transfer in'),
    ('transfer_out', 'Transfer out'),
    ('adjustment', 'Adjustment'),
]


class MosStockCard(models.TransientModel):
    """The client's Bin card (one bin, one item) and Stock card (one item across a warehouse, with its bins). Both are read from the
    stock move lines, the one ledger the stock balance also uses (FR-INV-014, FR-INV-023, FR-INV-024), so they can never disagree.
    A transfer between two bins of the scope shows as two rows (out and in) that cancel, and is left out of the totals."""

    _name = 'mos.stock.card'
    _description = 'MOS bin card and stock card'

    mode = fields.Selection([('bin', 'Bin card'), ('item', 'Stock card')], required=True, default='bin')
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', required=True, default=lambda s: s.env['stock.warehouse'].search([], limit=1))
    location_id = fields.Many2one('stock.location', string='Bin', domain="[('usage', '=', 'internal'), ('warehouse_id', '=', warehouse_id)]")
    product_id = fields.Many2one('product.product', string='Item', required=True)
    date_from = fields.Date(required=True, default=lambda s: fields.Date.context_today(s).replace(day=1))
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    uom_id = fields.Many2one(related='product_id.uom_id')
    searched = fields.Boolean(readonly=True, help='False until the first search, so the page does not show empty figures as results.')

    # What the search found.
    opening = fields.Float(digits=(14, 3), readonly=True)
    received = fields.Float(digits=(14, 3), readonly=True)
    issued = fields.Float(digits=(14, 3), readonly=True)
    closing = fields.Float(digits=(14, 3), readonly=True)
    current_stock = fields.Float(digits=(14, 3), readonly=True, help='On hand now, in every status.')
    ledger_gap = fields.Float(digits=(14, 3), readonly=True, help='On hand now minus the closing balance of a card that ends today; not zero when stock was set without a movement.')
    bin_count = fields.Integer(readonly=True)
    capacity = fields.Float(digits=(14, 0), readonly=True, help='The capacity of the silo, when the bin is one.')
    bin_parent = fields.Char(string='Storage location', readonly=True)
    line_count = fields.Integer(readonly=True)
    line_ids = fields.One2many('mos.stock.card.line', 'card_id', readonly=True)
    bin_ids = fields.One2many('mos.stock.card.bin', 'card_id', string='Stock by bin', readonly=True)

    @api.depends('mode', 'product_id')
    def _compute_display_name(self):
        labels = dict(self._fields['mode'].selection)
        for card in self:
            card.display_name = f'{labels[card.mode]}: {card.product_id.display_name}' if card.product_id else labels[card.mode]

    @api.onchange('warehouse_id')
    def _onchange_warehouse(self):
        if self.location_id and self.location_id.warehouse_id != self.warehouse_id:
            self.location_id = False

    # -- helpers --------------------------------------------------------------------------------------------------------------
    def _scope_locations(self):
        self.ensure_one()
        if self.mode == 'bin':
            if not self.location_id:
                raise UserError(self.env._('Choose the bin.'))
            root = self.location_id
        else:
            root = self.warehouse_id.view_location_id
        return self.env['stock.location'].sudo().search([('id', 'child_of', root.id), ('usage', '=', 'internal')])

    @api.model
    def _kind_in(self, source):
        if source.usage == 'inventory':
            return 'adjustment'
        if source.usage in ('internal', 'transit'):
            return 'transfer_in'
        return 'receipt'

    @api.model
    def _kind_out(self, dest):
        if dest.usage == 'inventory':
            return 'adjustment'
        if dest.usage in ('internal', 'transit'):
            return 'transfer_out'
        return 'issue'

    def _remark(self, kind, move_line, source, dest):
        partner = move_line.move_id.partner_id or move_line.picking_id.partner_id
        who = f' ({partner.name})' if partner else ''
        env = self.env
        if kind == 'receipt':
            return env._('Received from supplier%s', who) if partner else env._('Received from %s', source.name)
        if kind == 'adjustment':
            return env._('Stock adjustment')
        if kind == 'transfer_in':
            return env._('Transfer from %s', source.name)
        if kind == 'transfer_out':
            return env._('Transfer to %s', dest.name)
        if dest.usage == 'production':
            return env._('Issued to production')
        if dest.usage == 'customer':
            return env._('Issued to customer%s', who)
        if dest.usage == 'supplier':
            return env._('Returned to supplier%s', who)
        return env._('Issued to %s', dest.name)

    def _legs(self, scope_ids):
        """Every effect of a finished move line on the scope, oldest first: a list of dicts with the signed quantity."""
        self.ensure_one()
        lines = self.env['stock.move.line'].sudo().search(
            [
                ('state', '=', 'done'),
                ('product_id', '=', self.product_id.id),
                '|',
                ('location_id', 'in', scope_ids),
                ('location_dest_id', 'in', scope_ids),
            ],
            order='date, id',
        )
        legs = []
        for ml in lines:
            qty = ml.quantity_product_uom
            if not qty:
                continue
            src_in, dst_in = ml.location_id.id in scope_ids, ml.location_dest_id.id in scope_ids
            base = {'date': ml.date, 'reference': ml.reference or ml.move_id.reference or '', 'lot_id': ml.lot_id.id, 'user_id': ml.create_uid.id, 'move_line': ml}
            if dst_in and not src_in:
                kind = self._kind_in(ml.location_id)
                legs.append({**base, 'kind': kind, 'bin': ml.location_dest_id, 'qty': qty, 'internal': False, 'remarks': self._remark(kind, ml, ml.location_id, ml.location_dest_id)})
            elif src_in and not dst_in:
                kind = self._kind_out(ml.location_dest_id)
                legs.append({**base, 'kind': kind, 'bin': ml.location_id, 'qty': -qty, 'internal': False, 'remarks': self._remark(kind, ml, ml.location_id, ml.location_dest_id)})
            elif src_in and dst_in and ml.location_id != ml.location_dest_id:
                inner = self.mode == 'item'  # inside the warehouse a transfer is net zero and stays out of the totals
                legs.append({**base, 'kind': 'transfer_out', 'bin': ml.location_id, 'qty': -qty, 'internal': inner, 'remarks': self.env._('Transfer to %s', ml.location_dest_id.name)})
                legs.append({**base, 'kind': 'transfer_in', 'bin': ml.location_dest_id, 'qty': qty, 'internal': inner, 'remarks': self.env._('Transfer from %s', ml.location_id.name)})
        return legs

    # -- actions --------------------------------------------------------------------------------------------------------------
    def action_search(self):
        self.ensure_one()
        if self.date_to < self.date_from:
            raise UserError(self.env._('The end date is before the start date.'))
        scope = self._scope_locations()
        scope_ids = scope.ids
        start = datetime.combine(self.date_from, time.min)
        end = datetime.combine(self.date_to + timedelta(days=1), time.min)
        legs = self._legs(scope_ids)
        opening = sum(leg['qty'] for leg in legs if leg['date'] < start)
        shown = [leg for leg in legs if start <= leg['date'] < end]
        received = sum(leg['qty'] for leg in shown if leg['qty'] > 0 and not leg['internal'])
        issued = -sum(leg['qty'] for leg in shown if leg['qty'] < 0 and not leg['internal'])
        balance = opening
        rows = []
        for number, leg in enumerate(shown, 1):
            balance += leg['qty']
            rows.append(
                {
                    'card_id': self.id,
                    'number': number,
                    'date': leg['date'],
                    'reference': leg['reference'],
                    'kind': leg['kind'],
                    'lot_id': leg['lot_id'],
                    'location_id': leg['bin'].id,
                    'qty_in': max(leg['qty'], 0.0),
                    'qty_out': max(-leg['qty'], 0.0),
                    'balance': balance,
                    'remarks': leg['remarks'],
                    'user_id': leg['user_id'],
                }
            )
        quants = self.env['stock.quant'].sudo().search([('product_id', '=', self.product_id.id), ('location_id', 'in', scope_ids)])
        per_bin = {}
        for quant in quants:
            per_bin[quant.location_id.id] = per_bin.get(quant.location_id.id, 0.0) + quant.quantity
        silos = {s.location_id.id: s for s in self.env['mos.silo'].sudo().search([('location_id', 'in', scope_ids)])}
        bins = []
        if self.mode == 'item':
            for loc in scope.filtered(lambda l: per_bin.get(l.id)).sorted('complete_name'):
                silo = silos.get(loc.id)
                bins.append({'card_id': self.id, 'location_id': loc.id, 'quantity': per_bin[loc.id], 'capacity': silo.capacity_kg if silo else 0.0, 'area': loc.location_id.name})
        capacity, parent = 0.0, ''
        if self.mode == 'bin':
            silo = silos.get(self.location_id.id)
            capacity = silo.capacity_kg if silo else 0.0
            parent = self.location_id.location_id.name or ''
        self.line_ids.unlink()
        self.bin_ids.unlink()
        self.env['mos.stock.card.line'].create(rows)
        self.env['mos.stock.card.bin'].create(bins)
        on_hand = sum(per_bin.values())
        self.write(
            {
                'searched': True,
                'ledger_gap': round(on_hand - (opening + received - issued), 3) if self.date_to >= fields.Date.context_today(self) else 0.0,
                'opening': opening,
                'received': received,
                'issued': issued,
                'closing': opening + received - issued,
                'current_stock': sum(per_bin.values()),
                'bin_count': len(bins),
                'capacity': capacity,
                'bin_parent': parent,
                'line_count': len(rows),
            }
        )

    def action_reset(self):
        self.ensure_one()
        self.line_ids.unlink()
        self.bin_ids.unlink()
        self.write(
            {
                'searched': False,
                'product_id': False,
                'location_id': False,
                'date_from': fields.Date.context_today(self).replace(day=1),
                'date_to': fields.Date.context_today(self),
                'ledger_gap': 0, 'opening': 0, 'received': 0, 'issued': 0, 'closing': 0, 'current_stock': 0, 'bin_count': 0, 'capacity': 0, 'line_count': 0,
            }
        )

    def action_export(self):
        """The rows as a list, which has Odoo's own export of the shown columns to a spreadsheet."""
        self.ensure_one()
        if not self.searched:
            self.action_search()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('%s: %s', dict(self._fields['mode'].selection)[self.mode], self.product_id.display_name),
            'res_model': 'mos.stock.card.line',
            'view_mode': 'list',
            'domain': [('card_id', '=', self.id)],
        }

    def action_print(self):
        self.ensure_one()
        if not self.searched:
            self.action_search()
        return self.env.ref('mos_stock_cards.action_report_stock_card').report_action(self)


class MosStockCardLine(models.TransientModel):
    _name = 'mos.stock.card.line'
    _description = 'MOS stock card transaction'
    _order = 'card_id, number'

    card_id = fields.Many2one('mos.stock.card', required=True, ondelete='cascade')
    number = fields.Integer(string='No.')
    date = fields.Datetime()
    reference = fields.Char(string='Reference no.')
    kind = fields.Selection(KINDS, string='Transaction type')
    lot_id = fields.Many2one('stock.lot', string='Batch / lot no.')
    location_id = fields.Many2one('stock.location', string='Bin')
    uom_id = fields.Many2one(related='card_id.uom_id')
    qty_in = fields.Float(string='Qty in', digits=(14, 3))
    qty_out = fields.Float(string='Qty out', digits=(14, 3))
    balance = fields.Float(digits=(14, 3))
    remarks = fields.Char()
    user_id = fields.Many2one('res.users', string='Moved by')


class MosStockCardBin(models.TransientModel):
    _name = 'mos.stock.card.bin'
    _description = 'MOS stock card: stock by bin'
    _order = 'location_id'

    card_id = fields.Many2one('mos.stock.card', required=True, ondelete='cascade')
    location_id = fields.Many2one('stock.location', string='Bin')
    area = fields.Char(string='Storage location')
    quantity = fields.Float(digits=(14, 3))
    capacity = fields.Float(digits=(14, 0))
    uom_id = fields.Many2one(related='card_id.uom_id')
    fill_pct = fields.Float(string='Filled (%)', compute='_compute_fill', digits=(5, 1))
    state = fields.Selection([('in_stock', 'In stock'), ('empty', 'Empty')], compute='_compute_fill')

    @api.depends('quantity', 'capacity')
    def _compute_fill(self):
        for rec in self:
            rec.fill_pct = rec.quantity / rec.capacity * 100 if rec.capacity else 0.0
            rec.state = 'in_stock' if rec.quantity > 0 else 'empty'
