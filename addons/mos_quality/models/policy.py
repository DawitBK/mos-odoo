from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MosLabPolicy(models.Model):
    """What quality control asks for, per product or product category: whether a delivery is inspected at all, whether a good
    supplier is inspected one delivery in N, and whether a supplier certificate of analysis is needed (FR-QC-004, 017, 021)."""

    _name = 'mos.lab.policy'
    _inherit = ['mos.audited']
    _description = 'MOS QC policy'
    _order = 'product_id, categ_id'

    product_id = fields.Many2one('product.product', ondelete='cascade')
    categ_id = fields.Many2one('product.category', ondelete='cascade', string='Product category')
    inspection_required = fields.Boolean(default=True)
    skip_lot = fields.Boolean(string='Skip-lot for proven suppliers', help='A supplier with a clean run of inspections is inspected only one delivery in N.')
    clean_streak = fields.Integer(string='Clean inspections needed', default=5)
    skip_every = fields.Integer(string='Inspect one delivery in', default=4)
    coa_required = fields.Boolean(string="Supplier's certificate of analysis required")
    active = fields.Boolean(default=True)

    @api.constrains('product_id', 'categ_id', 'clean_streak', 'skip_every')
    def _check(self):
        for policy in self:
            if bool(policy.product_id) == bool(policy.categ_id):
                raise ValidationError(self.env._('A policy is for one product or one category, not both and not neither.'))
            if policy.skip_lot and (policy.clean_streak < 1 or policy.skip_every < 2):
                raise ValidationError(self.env._('Skip-lot needs a clean streak of at least 1 and "one in" at least 2.'))

    @api.model
    def for_product(self, product):
        policy = self.sudo().search([('product_id', '=', product.id)], limit=1)
        if policy:
            return policy
        categ = product.categ_id
        while categ:
            policy = self.sudo().search([('categ_id', '=', categ.id)], limit=1)
            if policy:
                return policy
            categ = categ.parent_id
        return self.browse()


class MosLabSkipCounter(models.Model):
    """Deliveries waved through since the last real inspection, per product and supplier."""

    _name = 'mos.lab.skip.counter'
    _description = 'MOS skip-lot counter'

    product_id = fields.Many2one('product.product', required=True, ondelete='cascade')
    supplier_id = fields.Many2one('res.partner', required=True, ondelete='cascade')
    waived_in_row = fields.Integer()

    _pair_uniq = models.Constraint('unique(product_id, supplier_id)', 'One counter per product and supplier.')


class MosGateVisit(models.Model):
    """The waiver is decided once per visit and stays decided (the same truck is never waved through and then inspected)."""

    _inherit = 'mos.gate.visit'

    qc_waived = fields.Boolean(readonly=True, copy=False)
    qc_waiver_note = fields.Char(readonly=True, copy=False)
    qc_waiver_checked = fields.Boolean(readonly=True, copy=False)

    def _mos_decide_waiver(self, product):
        """Work out, once, whether this delivery needs inspecting."""
        self.ensure_one()
        if self.qc_waiver_checked:
            return self.qc_waived
        policy = self.env['mos.lab.policy'].for_product(product)
        waived, note = False, ''
        if policy and not policy.inspection_required:
            waived, note = True, self.env._('policy: this product is not inspected')
        elif policy and policy.skip_lot and self.partner_id:
            history = self.env['mos.lab.inspection'].sudo().search(
                [('product_id', '=', product.id), ('visit_id.partner_id', '=', self.partner_id.id), ('state', '=', 'decided')], order='id desc', limit=policy.clean_streak
            )
            clean = len(history) >= policy.clean_streak and all(i.decision == 'accept' and i.suggested_outcome == 'pass' and not i.deduction_pct for i in history)
            counter = self.env['mos.lab.skip.counter'].sudo().search([('product_id', '=', product.id), ('supplier_id', '=', self.partner_id.id)], limit=1) or self.env['mos.lab.skip.counter'].sudo().create(
                {'product_id': product.id, 'supplier_id': self.partner_id.id}
            )
            if clean and counter.waived_in_row < policy.skip_every - 1:
                counter.waived_in_row += 1
                waived, note = True, self.env._('skip-lot: %(n)s clean inspections in a row, delivery %(k)s of %(every)s', n=policy.clean_streak, k=counter.waived_in_row, every=policy.skip_every)
            else:
                counter.waived_in_row = 0
        self.sudo().write({'qc_waiver_checked': True, 'qc_waived': waived, 'qc_waiver_note': note})
        if waived:
            self.env['mos.audit.event'].record('inspection_waived', self, f'{self.name}: {note}')
        return waived

    def _mos_inspection_waived(self):
        self.ensure_one()
        if self.purpose != 'inbound_po' or not self.purchase_id:
            return False
        try:
            product = self._po_product()
        except UserError:
            return False
        return self._mos_decide_waiver(product)

    def _mos_inspection_before_exit(self):
        return super()._mos_inspection_before_exit() and not self._mos_inspection_waived()

    def _exit_blockers(self):
        problems = super()._exit_blockers()
        if self.flow != 'B' and self._mos_inspection_waived() and self.grn_state != 'confirmed':
            problems.append(self.env._('the goods receipt is not confirmed'))
        return problems


class MosLabInspection(models.Model):
    _inherit = 'mos.lab.inspection'

    priority = fields.Selection([('normal', 'Normal'), ('urgent', 'Urgent')], default='normal', required=True)

    def decide(self, decision, deduction_pct=0.0, note=''):
        for insp in self:
            policy = self.env['mos.lab.policy'].for_product(insp.product_id)
            if policy and policy.coa_required and decision != 'reject':
                have = self.env['mos.attachment'].sudo().search_count([('res_model', '=', 'mos.gate.visit'), ('res_id', '=', insp.visit_id.id), ('kind', '=', 'coa')])
                if not have:
                    raise UserError(self.env._("%s needs the supplier's certificate of analysis attached to the delivery before the goods can be accepted.", insp.product_id.display_name))
        return super().decide(decision, deduction_pct, note)


class MosAttachment(models.Model):
    _inherit = 'mos.attachment'

    kind = fields.Selection([('other', 'Document'), ('photo', 'Photo'), ('coa', "Supplier's certificate of analysis")], default='other', required=True)
