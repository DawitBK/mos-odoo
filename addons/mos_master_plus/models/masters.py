from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

STORE_TYPES = [('raw', 'Raw materials'), ('finished', 'Finished goods'), ('spare', 'Spare parts'), ('packaging', 'Packaging'), ('hazmat', 'Hazardous materials')]
WAREHOUSE_TYPES = [('general', 'General purpose'), ('raw', 'Raw materials'), ('finished', 'Finished goods'), ('spare', 'Spare parts'), ('cold', 'Cold storage'), ('hazmat', 'Hazardous materials')]


def _is_weight(uom, kilogram):
    """Whether the unit converts to kilograms (the units of one family share the root of their tree)."""
    return uom == kilogram or bool(uom.parent_path and kilogram.parent_path and uom.parent_path.split('/')[0] == kilogram.parent_path.split('/')[0])


MATERIAL_TYPES = [('raw', 'Raw material'), ('packaging', 'Packaging'), ('finished', 'Finished goods'), ('spare', 'Spare parts'), ('consumable', 'Consumable'), ('other', 'Other')]


class StockWarehouse(models.Model):
    """The warehouse master of the client (page 17): what kind of warehouse it is, who keeps it, how much it can hold, and what may
    be received into it, issued from it and moved out of it. The flags and the capacity are enforced when a stock move is done."""

    _inherit = 'stock.warehouse'

    mos_type = fields.Selection(WAREHOUSE_TYPES, string='Warehouse type', default='general', required=True)
    mos_description = fields.Text(string='Description')
    mos_storekeeper_id = fields.Many2one('res.users', string='Storekeeper')
    mos_capacity_kg = fields.Float(string='Capacity (kg)', digits=(14, 0), help='What the warehouse can hold in all. Zero means no limit is kept.')
    mos_occupied_kg = fields.Float(string='Occupied (kg)', compute='_compute_mos_occupied', digits=(14, 0))
    mos_occupied_pct = fields.Float(string='Occupied (%)', compute='_compute_mos_occupied', digits=(5, 1))
    mos_temperature_controlled = fields.Boolean(string='Temperature controlled')
    mos_humidity_controlled = fields.Boolean(string='Humidity controlled')
    mos_allow_receipt = fields.Boolean(string='Receipts allowed', default=True)
    mos_allow_issue = fields.Boolean(string='Issues allowed', default=True)
    mos_allow_transfer = fields.Boolean(string='Transfers allowed', default=True)

    @api.constrains('mos_capacity_kg')
    def _check_mos_capacity(self):
        for warehouse in self:
            if warehouse.mos_capacity_kg < 0:
                raise ValidationError(self.env._('A capacity cannot be negative.'))

    @api.depends('view_location_id', 'mos_capacity_kg')
    def _compute_mos_occupied(self):
        for warehouse in self:
            held = warehouse.view_location_id._mos_held_kg() if warehouse.view_location_id else 0.0
            warehouse.mos_occupied_kg = held
            warehouse.mos_occupied_pct = (held / warehouse.mos_capacity_kg * 100.0) if warehouse.mos_capacity_kg else 0.0


class StockLocation(models.Model):
    """Stores and bins (client pages 18 and 20): a store is a storage area of a warehouse, a bin sits in a store. Both carry a code, a
    capacity, who keeps them and what may be received, issued and moved; Odoo's own locations carry none of this and are not limited."""

    _inherit = 'stock.location'

    mos_kind = fields.Selection([('store', 'Store'), ('bin', 'Bin')], string='Master record', index=True, copy=False)
    mos_code = fields.Char(string='Code')
    mos_description = fields.Text(string='Description')
    mos_warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse')
    mos_store_type = fields.Selection(STORE_TYPES, string='Store type')
    mos_storekeeper_id = fields.Many2one('res.users', string='Storekeeper')
    mos_capacity_kg = fields.Float(string='Capacity (kg)', digits=(14, 0), help='What the store or bin can hold. Zero means no limit is kept.')
    mos_occupied_kg = fields.Float(string='Occupied (kg)', compute='_compute_mos_occupied', digits=(14, 0))
    mos_occupied_pct = fields.Float(string='Occupied (%)', compute='_compute_mos_occupied', digits=(5, 1))
    mos_item_id = fields.Many2one('product.product', string='Item')
    mos_remarks = fields.Char(string='Remarks')
    mos_material_category_id = fields.Many2one('product.category', string='Material category')
    mos_temperature_controlled = fields.Boolean(string='Temperature controlled')
    mos_humidity_controlled = fields.Boolean(string='Humidity controlled')
    mos_restricted_access = fields.Boolean(string='Restricted access')
    mos_allow_receipt = fields.Boolean(string='Receipts allowed', default=True)
    mos_allow_issue = fields.Boolean(string='Issues allowed', default=True)
    mos_allow_transfer = fields.Boolean(string='Transfers allowed', default=True)

    _mos_code_uniq = models.Constraint('unique(mos_code, mos_warehouse_id, mos_kind)', 'This code is already used in the warehouse.')

    # -- the record ---------------------------------------------------------------------------------------------------------------
    @api.depends('mos_capacity_kg')
    def _compute_mos_occupied(self):
        for location in self:
            held = location._mos_held_kg() if location.id else 0.0
            location.mos_occupied_kg = held
            location.mos_occupied_pct = (held / location.mos_capacity_kg * 100.0) if location.mos_capacity_kg else 0.0

    @api.onchange('mos_kind', 'mos_warehouse_id')
    def _onchange_mos_warehouse(self):
        if self.mos_kind == 'store' and self.mos_warehouse_id:
            self.location_id = self.mos_warehouse_id.lot_stock_id
            self.usage = 'internal'
        elif self.mos_kind == 'bin':
            self.usage = 'internal'
            if self.location_id and self.location_id.mos_warehouse_id != self.mos_warehouse_id:
                self.location_id = False

    @api.onchange('mos_code')
    def _onchange_mos_code(self):
        if self.mos_kind == 'bin' and self.mos_code and not self.name:
            self.name = self.mos_code

    @api.onchange('location_id')
    def _onchange_mos_store(self):
        if self.mos_kind == 'bin' and self.location_id.mos_warehouse_id:
            self.mos_warehouse_id = self.location_id.mos_warehouse_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('mos_kind') == 'store' and vals.get('mos_warehouse_id') and not vals.get('location_id'):
                vals['location_id'] = self.env['stock.warehouse'].browse(vals['mos_warehouse_id']).lot_stock_id.id
            if vals.get('mos_kind') in ('store', 'bin'):
                vals.setdefault('usage', 'internal')
            if vals.get('mos_kind') == 'bin' and not vals.get('name'):
                vals['name'] = vals.get('mos_code')
        return super().create(vals_list)

    @api.constrains('mos_kind', 'mos_code', 'mos_warehouse_id', 'location_id', 'mos_capacity_kg', 'mos_item_id')
    def _check_mos_master(self):
        for location in self.filtered('mos_kind'):
            if not location.mos_code or not location.mos_warehouse_id:
                raise ValidationError(self.env._('A %s needs a code and a warehouse.', dict(self._fields['mos_kind'].selection)[location.mos_kind].lower()))
            if location.mos_capacity_kg < 0:
                raise ValidationError(self.env._('A capacity cannot be negative.'))
            if location.mos_kind == 'bin':
                store = location.location_id
                if store.mos_kind != 'store' or store.mos_warehouse_id != location.mos_warehouse_id:
                    raise ValidationError(self.env._('A bin sits in a store of its own warehouse.'))
            elif location.location_id.warehouse_id != location.mos_warehouse_id:
                raise ValidationError(self.env._('A store sits in the stock location of its warehouse.'))

    # -- what is held, and what may be done -------------------------------------------------------------------------------------------
    def _mos_held_kg(self):
        """What the stock ledger holds in this location and everything under it, in kilograms (goods counted in other units are left out)."""
        self.ensure_one()
        kilogram = self.env.ref('uom.product_uom_kgm')
        groups = self.env['stock.quant'].sudo()._read_group([('location_id', 'child_of', self.id)], ['product_id'], ['quantity:sum'])
        total = 0.0
        for product, quantity in groups:
            if _is_weight(product.uom_id, kilogram):
                total += product.uom_id._compute_quantity(quantity, kilogram)
        return max(total, 0.0)

    def _mos_chain(self):
        """This location and every location above it up to the warehouse."""
        chain, location = [], self
        while location:
            chain.append(location)
            location = location.location_id
        return chain

    def _mos_allows(self, operation):
        """False when the location, a store or bin above it, or its warehouse does not allow the operation (receipt, issue, transfer)."""
        self.ensure_one()
        field = 'mos_allow_%s' % operation
        if any(not location[field] for location in self._mos_chain() if location.mos_kind):
            return False
        return bool(self.warehouse_id[field]) if self.warehouse_id else True

    def _mos_contains(self, other):
        self.ensure_one()
        return bool(other.parent_path) and other.parent_path.startswith(self.parent_path)


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        self._mos_check_places()
        return super()._action_done(cancel_backorder=cancel_backorder)

    def _mos_check_places(self):
        """Receipts, issues and transfers keep to what the warehouse, store and bin allow, and nothing goes into a place beyond its capacity."""
        kilogram = self.env.ref('uom.product_uom_kgm')
        for move in self.filtered(lambda m: m.quantity > 0):
            source, destination = move.location_id, move.location_dest_id
            if source.usage == 'supplier' and not destination._mos_allows('receipt'):
                raise UserError(self.env._('Receipts are not allowed into %s.', destination.complete_name))
            if destination.usage in ('customer', 'production') and not source._mos_allows('issue'):
                raise UserError(self.env._('Issues are not allowed from %s.', source.complete_name))
            if source.usage == 'internal' and destination.usage == 'internal':
                for place in (source, destination):
                    if not place._mos_allows('transfer'):
                        raise UserError(self.env._('Transfers are not allowed out of or into %s.', place.complete_name))
            if destination.usage != 'internal' or not _is_weight(move.product_id.uom_id, kilogram):
                continue
            incoming_kg = move.uom_id._compute_quantity(move.quantity, kilogram)
            limits = [(place, place.mos_capacity_kg) for place in destination._mos_chain() if place.mos_kind and place.mos_capacity_kg]
            warehouse = destination.warehouse_id
            if warehouse.mos_capacity_kg:
                limits.append((warehouse.view_location_id, warehouse.mos_capacity_kg))
            for place, capacity in limits:
                if place._mos_contains(source):
                    continue  # it moves inside the place: nothing more is held
                held = place._mos_held_kg()
                if held + incoming_kg > capacity + 0.0001:
                    raise UserError(
                        self.env._(
                            '%(place)s holds %(held)s kg of %(capacity)s kg: %(qty)s kg more does not fit.',
                            place=place.complete_name,
                            held=round(held),
                            capacity=round(capacity),
                            qty=round(incoming_kg),
                        )
                    )
