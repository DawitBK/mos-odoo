import difflib
import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError


def _norm_name(name):
    return re.sub(r'[^a-z0-9]+', ' ', (name or '').lower()).strip()


class ResPartner(models.Model):
    """Business partners (FR-MD-005): one tax number belongs to one partner, and a name that looks like an existing partner is
    flagged. Individual suppliers, farmers and traders without a company, are registered by an identifier instead (FR-MD-014)."""

    _inherit = 'res.partner'

    mos_individual = fields.Boolean(string='Individual supplier', help='A farmer or trader without a company: a tax number is optional but an identifier is required.')
    mos_national_id = fields.Char(string='National ID or other identifier', help='For an individual supplier without a tax number.')
    mos_duplicate_warning = fields.Text(readonly=True, copy=False, string='Possible duplicates')

    @api.model
    def _mos_clean_tin(self, value):
        return re.sub(r'\s+', '', (value or '')).upper()

    @api.constrains('vat')
    def _check_tin_unique(self):
        for partner in self.filtered('vat'):
            tin = self._mos_clean_tin(partner.vat)
            if not tin:
                continue
            others = self.with_context(active_test=False).search([('vat', '!=', False), ('id', '!=', partner.id), ('commercial_partner_id', '!=', partner.commercial_partner_id.id)])
            twin = others.filtered(lambda o: self._mos_clean_tin(o.vat) == tin)[:1]
            if twin:
                raise ValidationError(self.env._('The tax number %(tin)s already belongs to %(who)s.', tin=partner.vat, who=twin.display_name))

    @api.constrains('mos_individual', 'vat', 'mos_national_id')
    def _check_individual_identifier(self):
        for partner in self:
            if partner.mos_individual and not (partner.vat or '').strip() and not (partner.mos_national_id or '').strip():
                raise ValidationError(self.env._('%s is an individual supplier with no tax number: record a national ID or other identifier.', partner.display_name))

    def _mos_similar_partners(self):
        """Other partners whose name is nearly the same as this one's (a typo, a different word order, a missing suffix)."""
        self.ensure_one()
        mine = _norm_name(self.name)
        if len(mine) < 4:
            return self.browse()
        candidates = self.with_context(active_test=False).search([('id', '!=', self.id), ('parent_id', '=', False), ('name', '!=', False)])
        similar = candidates.filtered(
            lambda c: (lambda theirs: bool(theirs) and (difflib.SequenceMatcher(None, mine, theirs).ratio() >= 0.85 or sorted(mine.split()) == sorted(theirs.split())))(_norm_name(c.name))
        )
        return similar[:5]

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        for partner in partners.filtered(lambda p: not p.parent_id and (p.supplier_rank or p.customer_rank)):
            similar = partner._mos_similar_partners()
            if similar:
                text = self.env._('Looks like %s. Check it is not the same business registered twice.', ', '.join(similar.mapped('display_name')))
                partner.sudo().mos_duplicate_warning = text
                partner.message_post(body=text)
        return partners

    def _mos_effective_wht_pct(self):
        """A supplier with no tax number is withheld at the rate the client confirmed for that case (system parameter
        `mos_master_plus.no_tin_wht_pct`); the usual rate otherwise (FR-MD-014)."""
        rate = super()._mos_effective_wht_pct()
        if self and self.mos_individual and not (self.vat or '').strip():
            configured = self.env['ir.config_parameter'].sudo().get_str('mos_master_plus.no_tin_wht_pct', '')
            if configured not in ('', False):
                try:
                    return float(configured)
                except ValueError:
                    return rate
        return rate
