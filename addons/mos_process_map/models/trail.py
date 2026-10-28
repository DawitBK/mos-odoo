from odoo import api, fields, models
from odoo.exceptions import AccessError


class MosProcessTrail(models.TransientModel):
    """Every document that belongs to one purchase or sales order, in the order the work happened: the request that started it, the
    quotes, the amendments, each truck with its weighing, inspection and receipt, the invoices, vouchers and payments, the returns.
    What a person may not read is left out; each line opens its document."""

    _name = 'mos.process.trail'
    _description = 'MOS document trail'

    name = fields.Char(readonly=True)
    res_model = fields.Char(readonly=True)
    res_id = fields.Integer(readonly=True)
    line_ids = fields.One2many('mos.process.trail.line', 'trail_id', readonly=True)

    @api.model
    def _state_label(self, record):
        field = record._fields.get('state')
        if field is None or field.type != 'selection' or not record.state:
            return ''
        return dict(field._description_selection(self.env)).get(record.state, record.state)

    @api.model
    def _build(self, order):
        """The trail of ``order`` (a purchase.order or a sale.order) as a list of line values."""
        env = self.env
        none = env['res.users']
        lines = []

        def add(stage, record, note=''):
            for rec in record or []:
                if not rec.has_access('read'):
                    continue  # linked, but not something this person may open
                lines.append(
                    {
                        'stage': stage,
                        'res_model': rec._name,
                        'res_id': rec.id,
                        'name': rec.display_name,
                        'status': self._state_label(rec),
                        'note': note,
                        'when': rec.create_date,
                        'by_id': rec.create_uid.id,
                    }
                )

        def found(model, domain):
            if model not in env:
                return none
            try:
                return env[model].search(domain, order='id')
            except (AccessError, ValueError):
                return none

        if order._name == 'purchase.order':
            add('Purchase request', getattr(order, 'mos_request_id', none))
            add('Request for quotation', found('mos.rfq', [('order_id', '=', order.id)]))
            for quote in getattr(order, 'mos_quote_ids', []):
                add('Quote', quote, f'{quote.partner_id.display_name}: {quote.amount:,.2f}')
            add('Contract', getattr(order, 'mos_contract_id', none))
            add('Purchase order', order, f'{order.partner_id.display_name}: {order.amount_total:,.2f}')
            add('Amendment', found('mos.po.amendment', [('purchase_id', '=', order.id)]))
            for visit in found('mos.gate.visit', [('purchase_id', '=', order.id)]):
                add('Truck at the gate', visit, visit.plate or '')
                add('Weigh ticket', visit.ticket_id)
                add('Lab inspection', getattr(visit, 'inspection_id', none))
                add('Certificate of analysis', getattr(visit, 'coa_ids', none))
                add('Goods receipt', getattr(visit, 'grn_picking_id', none))
            add('Receipt moved here', found('mos.grn.reassignment', [('to_order_id', '=', order.id)]))
            add('Service receipt', found('mos.service.receipt', [('order_id', '=', order.id)]))
            invoices = found('mos.supplier.invoice', [('purchase_id', '=', order.id)])
            add('Supplier invoice', invoices)
            add('Invoice sent by the supplier', found('mos.supplier.submission', [('order_id', '=', order.id)]))
            add('Return to the supplier', found('mos.supplier.return', [('purchase_id', '=', order.id)]))
            if invoices:
                add('Debit note', found('mos.debit.note', [('invoice_id', 'in', invoices.ids)]))
                vouchers = found('mos.payment.voucher.line', [('invoice_id', 'in', invoices.ids)]).voucher_id
                add('Payment voucher', vouchers)
                for voucher in vouchers:
                    add('Payment', getattr(voucher, 'payment_ids', none))
        else:
            add('Sales order', order, f'{order.partner_id.display_name}: {order.amount_total:,.2f}')
            for delivery in found('mos.delivery.order', [('sale_id', '=', order.id)]):
                add('Delivery order', delivery, delivery.plate or '')
                add('Truck at the gate', delivery.visit_id)
                add('Weigh ticket', delivery.visit_id.ticket_id)
                add('Customer invoice', delivery.invoice_id)
                if delivery.invoice_id:
                    add('Receipt', found('mos.receipt', [('invoice_id', '=', delivery.invoice_id.id)]))
                    add('Credit note', found('mos.credit.note', [('invoice_id', '=', delivery.invoice_id.id)]))
                add('Customer return', found('mos.customer.return', [('delivery_id', '=', delivery.id)]))
        return lines

    @api.model
    def open_for(self, order):
        order.ensure_one()
        trail = self.create(
            {
                'name': self.env._('Document trail of %s', order.display_name),
                'res_model': order._name,
                'res_id': order.id,
                'line_ids': [(0, 0, dict(values, sequence=number)) for number, values in enumerate(self._build(order))],
            }
        )
        return {'type': 'ir.actions.act_window', 'name': trail.name, 'res_model': self._name, 'res_id': trail.id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}


class MosProcessTrailLine(models.TransientModel):
    _name = 'mos.process.trail.line'
    _description = 'MOS document trail line'
    _order = 'sequence, id'

    trail_id = fields.Many2one('mos.process.trail', required=True, ondelete='cascade')
    sequence = fields.Integer()
    stage = fields.Char()
    res_model = fields.Char()
    res_id = fields.Integer()
    name = fields.Char(string='Document')
    status = fields.Char()
    note = fields.Char()
    when = fields.Datetime()
    by_id = fields.Many2one('res.users', string='Created by')

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def action_mos_trail(self):
        return self.env['mos.process.trail'].open_for(self)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_mos_trail(self):
        return self.env['mos.process.trail'].open_for(self)
