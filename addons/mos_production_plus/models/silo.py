from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosSiloStrapping(models.Model):
    """The calibration table of a silo: what height of grain means what volume. A dip reading is turned into a volume by
    interpolating between its rows (FR-INV-020)."""

    _name = 'mos.silo.strapping'
    _description = 'MOS silo strapping table'
    _order = 'silo_id, height_cm'

    silo_id = fields.Many2one('mos.silo', required=True, ondelete='cascade')
    height_cm = fields.Float(required=True, digits=(8, 1))
    volume_m3 = fields.Float(required=True, digits=(10, 3))

    _row_uniq = models.Constraint('unique(silo_id, height_cm)', 'A height appears once in a strapping table.')

    @api.constrains('height_cm', 'volume_m3')
    def _check(self):
        for row in self:
            if row.height_cm < 0 or row.volume_m3 < 0:
                raise ValidationError(self.env._('Height and volume cannot be negative.'))


class MosSilo(models.Model):
    _inherit = 'mos.silo'

    strapping_ids = fields.One2many('mos.silo.strapping', 'silo_id')
    bulk_density = fields.Float(string='Bulk density (kg/m3)', default=780.0, digits=(8, 1), help='Used to turn a dip volume into kilograms.')
    dip_ids = fields.One2many('mos.silo.dip', 'silo_id')
    hold_ids = fields.One2many('mos.silo.hold', 'silo_id')
    on_hold = fields.Boolean(compute='_compute_on_hold')

    def _compute_on_hold(self):
        for silo in self:
            silo.on_hold = bool(silo.hold_ids.filtered(lambda h: h.state == 'active'))

    def volume_at(self, height_cm):
        """Volume at a dip height, interpolated between strapping rows."""
        self.ensure_one()
        rows = self.strapping_ids.sorted('height_cm')
        if len(rows) < 2:
            raise UserError(self.env._('Silo %s has no strapping table (at least two rows).', self.code))
        if height_cm < rows[0].height_cm or height_cm > rows[-1].height_cm:
            raise UserError(self.env._('A height of %(h)s cm is outside the strapping table of silo %(code)s (%(lo)s to %(hi)s cm).', h=height_cm, code=self.code, lo=rows[0].height_cm, hi=rows[-1].height_cm))
        for low, high in zip(rows, rows[1:]):
            if low.height_cm <= height_cm <= high.height_cm:
                span = high.height_cm - low.height_cm
                share = (height_cm - low.height_cm) / span if span else 0.0
                return low.volume_m3 + share * (high.volume_m3 - low.volume_m3)
        return rows[-1].volume_m3


class MosSiloDip(models.Model):
    """A physical reading of how full a silo is, set against what the ledger says. A difference beyond the tolerance becomes an
    exception and can be turned into a stock adjustment (FR-INV-020)."""

    _name = 'mos.silo.dip'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS silo dip reading'
    _order = 'date desc, id desc'
    _mos_final_states = ('posted',)
    _mos_editable_after_post = {'adjustment_id'}

    name = fields.Char(readonly=True, copy=False, default='New')
    silo_id = fields.Many2one('mos.silo', required=True)
    site_id = fields.Many2one(related='silo_id.site_id', store=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    height_cm = fields.Float(string='Dip height (cm)', required=True, digits=(8, 1))
    density = fields.Float(string='Density (kg/m3)', digits=(8, 1))
    volume_m3 = fields.Float(readonly=True, digits=(10, 3))
    measured_kg = fields.Float(string='Measured (kg)', readonly=True, digits=(14, 0))
    ledger_kg = fields.Float(string='Ledger says (kg)', readonly=True, digits=(14, 0))
    variance_kg = fields.Float(readonly=True, digits=(14, 0))
    variance_pct = fields.Float(readonly=True, digits=(6, 2))
    within_tolerance = fields.Boolean(readonly=True)
    read_by_id = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    adjustment_id = fields.Many2one('mos.stock.adjustment', readonly=True)
    state = fields.Selection([('posted', 'Posted')], default='posted', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        tolerance = self.env['ir.config_parameter'].sudo().get_float('mos_production_plus.dip_tolerance_pct', 1.0)
        for vals in vals_list:
            silo = self.env['mos.silo'].browse(vals['silo_id'])
            density = vals.get('density') or silo.bulk_density
            volume = silo.volume_at(vals['height_cm'])
            measured = volume * density
            ledger = silo.fresh().level_kg
            variance = measured - ledger
            pct = (variance / ledger * 100.0) if ledger else (100.0 if measured else 0.0)
            vals.update(
                {
                    'density': density,
                    'volume_m3': round(volume, 3),
                    'measured_kg': round(measured),
                    'ledger_kg': round(ledger),
                    'variance_kg': round(variance),
                    'variance_pct': round(pct, 2),
                    'within_tolerance': abs(pct) <= tolerance,
                }
            )
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.silo.dip')
        dips = super().create(vals_list)
        for dip in dips.filtered(lambda d: not d.within_tolerance):
            self.env['mos.exception'].raise_for(
                'dip_variance', dip, self.env._('%(silo)s: dip %(m)s kg, ledger %(l)s kg (%(p).1f%%)', silo=dip.silo_id.code, m=round(dip.measured_kg), l=round(dip.ledger_kg), p=dip.variance_pct), severity='medium', due_days=2
            )
        return dips

    def action_adjust(self):
        """Draft the stock adjustment that brings the ledger to the measured level; it still needs its own approval."""
        self.ensure_one()
        if self.adjustment_id:
            return self.adjustment_id
        silo = self.silo_id
        product = silo.product_id
        if not product:
            raise UserError(self.env._('The silo has no grain product set.'))
        reason = self.env['mos.reason'].sudo().search([('domain', '=', 'count')], limit=1) or self.env['mos.reason'].sudo().create({'domain': 'count', 'code': 'COUNT', 'name': self.env._('Count difference')})
        adjustment = self.env['mos.stock.adjustment'].create(
            {
                'site_id': silo.site_id.id,
                'location_id': silo.location_id.id,
                'reason_id': reason.id,
                'note': self.env._('Dip %s', self.name),
                'line_ids': [Command.create({'product_id': product.id, 'new_qty': max(self.measured_kg, 0.0)})],
            }
        )
        self.sudo().write({'adjustment_id': adjustment.id})
        return adjustment


class MosSiloHold(models.Model):
    """A silo that is being fumigated or treated cannot be drawn from or filled until the hold is released (FR-INV-021)."""

    _name = 'mos.silo.hold'
    _inherit = ['mos.audited', 'mail.thread']
    _description = 'MOS silo treatment hold'
    _order = 'id desc'

    name = fields.Char(readonly=True, copy=False, default='New')
    silo_id = fields.Many2one('mos.silo', required=True)
    site_id = fields.Many2one(related='silo_id.site_id', store=True)
    reason = fields.Selection([('fumigation', 'Fumigation'), ('treatment', 'Insecticide or other treatment'), ('cleaning', 'Cleaning'), ('inspection', 'Inspection')], required=True, default='fumigation')
    chemical = fields.Char(string='Product used')
    started_at = fields.Datetime(default=fields.Datetime.now, required=True)
    planned_end = fields.Datetime(string='Safe to use again from', required=True)
    released_at = fields.Datetime(readonly=True)
    released_by_id = fields.Many2one('res.users', readonly=True)
    release_note = fields.Char(readonly=True)
    state = fields.Selection([('active', 'On hold'), ('released', 'Released')], default='active', required=True, readonly=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('mos.silo.hold')
        holds = super().create(vals_list)
        for hold in holds:
            if self.search_count([('silo_id', '=', hold.silo_id.id), ('state', '=', 'active'), ('id', '!=', hold.id)]):
                raise ValidationError(self.env._('Silo %s is already on hold.', hold.silo_id.code))
            if hold.planned_end <= hold.started_at:
                raise ValidationError(self.env._('The hold must end after it starts.'))
        return holds

    @api.model
    def is_held(self, silo):
        return bool(self.sudo().search_count([('silo_id', '=', silo.id), ('state', '=', 'active')]))

    def action_release(self, note=''):
        user = self.env.user
        if not (user.has_group('mos_inventory.group_mos_inventory_manager') or user.has_group('mos_lab.group_mos_lab_manager') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only an inventory or lab manager can release a silo.'))
        for hold in self:
            if hold.state != 'active':
                raise UserError(self.env._('This hold is already released.'))
            if fields.Datetime.now() < hold.planned_end and not note:
                raise UserError(self.env._('The safe date (%s) has not come: give the reason for releasing early.', hold.planned_end))
            hold.write({'state': 'released', 'released_at': fields.Datetime.now(), 'released_by_id': user.id, 'release_note': note})
            self.env['mos.audit.event'].record('silo_released', hold, f'{hold.silo_id.code}: {note}')


class MosMillingRunHold(models.Model):
    _inherit = 'mos.milling.run'

    def action_complete(self):
        for run in self.filtered(lambda r: r.state == 'draft'):
            for silo in run.input_ids.silo_id:
                if self.env['mos.silo.hold'].is_held(silo):
                    raise UserError(self.env._('Silo %s is on a treatment hold: wheat cannot be drawn from it.', silo.code))
        return super().action_complete()


class MosGateVisitHold(models.Model):
    _inherit = 'mos.gate.visit'

    def _mos_preflight(self, product=None, qty=0.0):
        problems = super()._mos_preflight(product, qty)
        if self.silo_id and self.env['mos.silo.hold'].is_held(self.silo_id):
            problems.append(self.env._('silo %s is on a treatment hold', self.silo_id.code))
        return problems


class MosSiloReleaseWizard(models.TransientModel):
    _name = 'mos.silo.release.wizard'
    _description = 'MOS release a silo from its hold'

    hold_id = fields.Many2one('mos.silo.hold', required=True, readonly=True)
    note = fields.Char(help='Required when the safe date has not come yet.')

    def action_confirm(self):
        self.ensure_one()
        self.hold_id.action_release(self.note or '')
        return {'type': 'ir.actions.act_window_close'}


class MosSiloHoldUi(models.Model):
    _inherit = 'mos.silo.hold'

    def action_open_release(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Release the silo'),
            'res_model': 'mos.silo.release.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_hold_id': self.id},
        }
