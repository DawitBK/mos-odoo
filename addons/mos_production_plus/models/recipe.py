from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosRecipe(models.Model):
    """A recipe for a flour: which wheats go in and in what shares (the grist), and the extraction it should give. Versioned and
    approved; an approved version never changes, a change is a new version (FR-MFG-003, 015)."""

    _name = 'mos.recipe'
    _inherit = ['mos.posted', 'mail.thread']
    _description = 'MOS milling recipe'
    _order = 'product_id, version desc'
    _mos_final_states = ('approved', 'retired')
    _mos_editable_after_post = {'state', 'approved_by_id'}

    name = fields.Char(compute='_compute_name', store=True)
    product_id = fields.Many2one('product.product', required=True, string='Main flour')
    version = fields.Integer(default=1, readonly=True, required=True)
    state = fields.Selection([('draft', 'Draft'), ('approved', 'Approved'), ('retired', 'Retired')], default='draft', required=True, readonly=True, tracking=True)
    line_ids = fields.One2many('mos.recipe.line', 'recipe_id', string='Grist')
    target_extraction_pct = fields.Float(string='Expected extraction (%)', default=75.0, digits=(5, 2))
    extraction_tolerance = fields.Float(string='Extraction tolerance (points)', default=1.5, digits=(4, 2))
    grist_tolerance = fields.Float(string='Grist tolerance (points per wheat)', default=5.0, digits=(4, 2))
    note = fields.Text()
    approved_by_id = fields.Many2one('res.users', readonly=True)

    _version_uniq = models.Constraint('unique(product_id, version)', 'This recipe version already exists.')

    @api.depends('product_id', 'version')
    def _compute_name(self):
        for recipe in self:
            recipe.name = f'{recipe.product_id.display_name or "?"} v{recipe.version}'

    @api.constrains('target_extraction_pct')
    def _check_extraction(self):
        for recipe in self:
            if not 0 < recipe.target_extraction_pct <= 100:
                raise ValidationError(self.env._('Extraction is between 0 and 100 percent.'))

    def action_approve(self):
        user = self.env.user
        if not (user.has_group('mos_manufacturing.group_mos_production_supervisor') or user.has_group('mos_core.group_mos_admin')):
            raise UserError(self.env._('Only a production supervisor can approve a recipe.'))
        for recipe in self:
            if recipe.state != 'draft':
                raise UserError(self.env._('Only a draft recipe can be approved.'))
            total = sum(recipe.line_ids.mapped('share_pct'))
            if not recipe.line_ids or abs(total - 100.0) > 0.01:
                raise UserError(self.env._('The grist must add up to 100 percent (it adds up to %s).', round(total, 2)))
            if recipe.create_uid == user and not user.has_group('mos_core.group_mos_admin'):
                raise UserError(self.env._('The author of a recipe cannot approve it.'))
            self.search([('product_id', '=', recipe.product_id.id), ('state', '=', 'approved'), ('id', '!=', recipe.id)]).write({'state': 'retired'})
            recipe.write({'state': 'approved', 'approved_by_id': user.id})
            self.env['mos.audit.event'].record('recipe_approved', recipe, recipe.name)

    def action_new_version(self):
        self.ensure_one()
        if self.state == 'draft':
            raise UserError(self.env._('A draft is edited in place.'))
        top = self.search([('product_id', '=', self.product_id.id)], order='version desc', limit=1)
        return self.create(
            {
                'product_id': self.product_id.id,
                'version': top.version + 1,
                'target_extraction_pct': self.target_extraction_pct,
                'extraction_tolerance': self.extraction_tolerance,
                'grist_tolerance': self.grist_tolerance,
                'line_ids': [Command.create({'wheat_id': line.wheat_id.id, 'share_pct': line.share_pct}) for line in self.line_ids],
            }
        )

    @api.model
    def in_force(self, product):
        return self.search([('product_id', '=', product.id), ('state', '=', 'approved')], limit=1)


class MosRecipeLine(models.Model):
    _name = 'mos.recipe.line'
    _description = 'MOS recipe grist line'
    _order = 'id'

    recipe_id = fields.Many2one('mos.recipe', required=True, ondelete='cascade')
    wheat_id = fields.Many2one('product.product', required=True, string='Wheat')
    share_pct = fields.Float(string='Share (%)', required=True, digits=(5, 2))

    _wheat_once = models.Constraint('unique(recipe_id, wheat_id)', 'A wheat appears once in a recipe.')

    @api.constrains('share_pct')
    def _check(self):
        for line in self:
            if not 0 < line.share_pct <= 100:
                raise ValidationError(self.env._('A share is between 0 and 100 percent.'))

    def _guard(self):
        if any(line.recipe_id.state != 'draft' for line in self):
            raise UserError(self.env._('The grist of an approved recipe cannot change; create a new version.'))

    @api.model_create_multi
    def create(self, vals_list):
        recipes = self.env['mos.recipe'].browse([v['recipe_id'] for v in vals_list if v.get('recipe_id')])
        if any(r.state != 'draft' for r in recipes):
            raise UserError(self.env._('The grist of an approved recipe cannot change; create a new version.'))
        return super().create(vals_list)

    def write(self, vals):
        self._guard()
        return super().write(vals)

    def unlink(self):
        self._guard()
        return super().unlink()


class MosMillingRun(models.Model):
    """A run follows a recipe: its grist and extraction are compared with the recipe's when the run is completed (FR-MFG-015)."""

    _inherit = 'mos.milling.run'

    recipe_id = fields.Many2one('mos.recipe', domain="[('state', '=', 'approved')]")
    work_order_id = fields.Many2one('mos.work.order', readonly=True)

    def _mos_grist_problems(self):
        self.ensure_one()
        recipe = self.recipe_id
        if not recipe:
            return []
        problems = []
        total = sum(self.input_ids.mapped('qty_kg')) or 1.0
        actual = {}
        for line in self.input_ids:
            actual[line.product_id] = actual.get(line.product_id, 0.0) + line.qty_kg / total * 100.0
        for line in recipe.line_ids:
            diff = actual.get(line.wheat_id, 0.0) - line.share_pct
            if abs(diff) > recipe.grist_tolerance:
                problems.append(self.env._('%(wheat)s is %(have).1f%% of the grist, the recipe says %(want)s%%', wheat=line.wheat_id.display_name, have=actual.get(line.wheat_id, 0.0), want=line.share_pct))
        for wheat, share in actual.items():
            if wheat not in recipe.line_ids.wheat_id:
                problems.append(self.env._('%(wheat)s (%(share).1f%%) is not in the recipe', wheat=wheat.display_name, share=share))
        flour = sum(self.output_ids.filtered(lambda o: o.kind == 'flour' and o.product_id == recipe.product_id).mapped('qty_kg'))
        if flour:
            extraction = flour / total * 100.0
            if abs(extraction - recipe.target_extraction_pct) > recipe.extraction_tolerance:
                problems.append(self.env._('extraction %(have).2f%% against the recipe %(want).2f%%', have=extraction, want=recipe.target_extraction_pct))
        return problems

    def action_complete(self):
        enforce = self.env['ir.config_parameter'].sudo().get_bool('mos_production_plus.enforce_grist')
        notes = {}
        for run in self.filtered(lambda r: r.state == 'draft' and r.recipe_id):
            problems = run._mos_grist_problems()
            if problems and enforce:
                raise UserError(self.env._('The run does not follow recipe %(recipe)s: %(why)s.', recipe=run.recipe_id.name, why='; '.join(problems)))
            notes[run.id] = '; '.join(problems)
        result = super().action_complete()
        for run in self.filtered(lambda r: r.id in notes):
            if notes[run.id]:
                run.sudo().message_post(body=self.env._('Off recipe: %s', notes[run.id]))
                self.env['mos.exception'].raise_for('off_recipe', run, self.env._('%(run)s off recipe: %(why)s', run=run.name, why=notes[run.id][:150]), severity='low', due_days=3)
        return result
