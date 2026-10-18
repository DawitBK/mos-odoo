from odoo import api, fields, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mos_self_billing = fields.Boolean(string='Self-billing supplier', help='For a farmer or trader who issues no invoice: the mill raises the purchase voucher from the receipt (FR-AP-006).')


class MosSupplierInvoice(models.Model):
    """A voucher the mill raised itself from a receipt (FR-AP-006, 018)."""

    _inherit = 'mos.supplier.invoice'

    self_billed = fields.Boolean(readonly=True, copy=False)
    source_visit_id = fields.Many2one('mos.gate.visit', readonly=True, copy=False, ondelete='restrict')

    _self_bill_once = models.Constraint('unique(source_visit_id)', 'A receipt is self-billed once.')


class MosGateVisit(models.Model):
    """After a receipt for a self-billing supplier is confirmed, the purchase voucher is raised at the payable quantity and the
    order price (as adjusted for quality), with no input VAT unless the supplier is VAT-registered (FR-AP-006, 018)."""

    _inherit = 'mos.gate.visit'

    self_bill_id = fields.Many2one('mos.supplier.invoice', readonly=True, copy=False)

    def _mos_after_confirm(self):
        super()._mos_after_confirm()
        for visit in self.filtered(lambda v: v.flow != 'B'):
            if visit.sudo().purchase_id.partner_id.mos_self_billing:
                visit._self_bill()

    def _self_bill(self):
        self.ensure_one()
        if self.self_bill_id:
            return self.self_bill_id
        order = self.sudo().purchase_id
        partner = order.partner_id
        insp = self.inspection_id
        product = insp.product_id if insp else self._po_product()
        qty = insp.payable_qty if (insp and insp.state == 'decided') else self.net_weight
        lines = order.order_line.filtered(lambda line: line.product_id == product)
        price = lines[:1].price_unit
        if insp:
            price = self.env['mos.supplier.invoice']._mos_expected_price(order, product, price) if insp.price_adjust_pct else price
        if not qty or not price:
            raise UserError(self.env._('There is no quantity or price to self-bill.'))
        voucher_vat = partner.mos_vat_pct if partner.vat else 0.0
        invoice = self.env['mos.supplier.invoice'].sudo().create(
            {
                'supplier_id': partner.id,
                'supplier_ref': f'SB-{self.name}',
                'purchase_id': order.id,
                'product_id': product.id,
                'qty': qty,
                'unit_price': price,
                'vat_pct': voucher_vat,
                'wht_pct': partner.mos_wht_pct,
                'self_billed': True,
                'source_visit_id': self.id,
            }
        )
        invoice.action_match()
        self.sudo().write({'self_bill_id': invoice.id})
        self.env['mos.audit.event'].record('self_billed', self, f'{self.name}: {invoice.name}, {qty} kg at {price}')
        return invoice
