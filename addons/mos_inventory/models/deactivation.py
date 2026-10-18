from odoo import models
from odoo.exceptions import UserError


class ProductProduct(models.Model):
    """A product that still has stock, open orders or an open inspection cannot be archived (FR-MD-011)."""

    _inherit = 'product.product'

    def _mos_archive_blockers(self):
        self.ensure_one()
        problems = []
        if self.is_storable and self.sudo().qty_available:
            problems.append(self.env._('%s units are in stock', self.sudo().qty_available))
        lines = self.env['purchase.order.line'].sudo().search([('product_id', '=', self.id), ('order_id.state', 'in', ('draft', 'sent', 'to approve', 'purchase'))])
        open_lines = len(lines.filtered(lambda line: line.qty_received < line.product_qty))
        if open_lines:
            problems.append(self.env._('%s purchase order lines are still open', open_lines))
        inspections = self.env['mos.lab.inspection'].sudo().search_count([('product_id', '=', self.id), ('state', '!=', 'decided')])
        if inspections:
            problems.append(self.env._('%s inspections are undecided', inspections))
        return problems

    def write(self, vals):
        if vals.get('active') is False:
            for product in self.filtered('active'):
                problems = product._mos_archive_blockers()
                if problems:
                    raise UserError(self.env._('%(name)s cannot be archived: %(why)s.', name=product.display_name, why='; '.join(problems)))
        return super().write(vals)


class ResPartner(models.Model):
    """A supplier with open orders, unpaid invoices or a vehicle on site cannot be archived."""

    _inherit = 'res.partner'

    def _mos_archive_blockers(self):
        self.ensure_one()
        problems = []
        orders = self.env['purchase.order'].sudo().search_count([('partner_id', '=', self.id), ('state', 'in', ('draft', 'sent', 'to approve', 'purchase'))])
        if orders:
            problems.append(self.env._('%s purchase orders are open', orders))
        invoices = self.env['mos.supplier.invoice'].sudo().search_count([('supplier_id', '=', self.id), ('state', 'in', ('draft', 'payable', 'vouchered'))])
        if invoices:
            problems.append(self.env._('%s supplier invoices are unpaid', invoices))
        visits = self.env['mos.gate.visit'].sudo().search_count([('partner_id', '=', self.id), ('state', 'in', ('expected', 'arrived', 'weighing', 'weighed', 'cleared'))])
        if visits:
            problems.append(self.env._('%s vehicles are on site or expected', visits))
        return problems

    def write(self, vals):
        if vals.get('active') is False:
            for partner in self.filtered('active'):
                problems = partner._mos_archive_blockers()
                if problems:
                    raise UserError(self.env._('%(name)s cannot be archived: %(why)s.', name=partner.display_name, why='; '.join(problems)))
        return super().write(vals)
