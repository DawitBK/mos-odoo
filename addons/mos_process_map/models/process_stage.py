from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

LANES = [('buy', 'Buy and receive'), ('make', 'Make'), ('sell', 'Sell and collect'), ('control', 'Control')]


class MosProcessStage(models.TransientModel):
    """One stage of the mill's work on the process map. The board is rebuilt each time it is opened, from the same records the screens
    show, so what a card says is what its list holds. A person sees only the stages whose records they may read."""

    _name = 'mos.process.stage'
    _description = 'MOS process map stage'
    _order = 'lane, sequence, id'

    sequence = fields.Integer()
    lane = fields.Selection(LANES, required=True)
    name = fields.Char(required=True)
    description = fields.Char()
    caption = fields.Char()
    res_model = fields.Char(required=True)
    open_domain = fields.Text(required=True, default='[]')
    count = fields.Integer(string='Waiting')
    attention = fields.Integer(string='Need attention')
    total = fields.Integer(string='In all')
    attention_domain = fields.Text()

    # -- what the board is made of --------------------------------------------------------------------------------------------------
    @api.model
    def _stages(self):
        """(lane, name, what happens here, model, waiting domain, caption, attention domain, domain for the total). The order is the order of the work."""
        month = fields.Date.to_string(fields.Date.context_today(self) - timedelta(days=30))
        week = fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=7))
        return [
            # buy and receive
            ('buy', 'Suppliers', 'Register, check the documents, approve. A bank change needs three people.', 'res.partner', [('supplier_rank', '>', 0), ('mos_supplier_state', '=', 'candidate')], 'to check and approve', [('supplier_rank', '>', 0), ('mos_supplier_state', 'in', ('suspended', 'blocked'))], [('supplier_rank', '>', 0)]),
            ('buy', 'Purchase requests', 'Raised by a department, approved by the matrix, then ordered.', 'mos.purchase.request', [('state', 'in', ('draft', 'pending', 'approved'))], 'not ordered yet', [('state', '=', 'pending')], []),
            ('buy', 'Requests for quotation', 'Suppliers quote, the lowest wins unless a reason is given.', 'mos.rfq', [('state', 'in', ('draft', 'sent'))], 'out for quotes', [], []),
            ('buy', 'Purchase orders', 'Confirmed orders waiting for the truck. A change goes through an amendment.', 'purchase.order', [('state', '=', 'purchase'), ('receipt_status', '!=', 'full')], 'waiting for delivery', [], [('state', '=', 'purchase')]),
            ('buy', 'Gate: trucks coming in', 'Arrival, shift check, watch list, entry.', 'mos.gate.visit', [('purpose', '=', 'inbound_po'), ('state', 'in', ('expected', 'arrived', 'weighing'))], 'expected or on site', [], [('purpose', '=', 'inbound_po')]),
            ('buy', 'Weighbridge', 'Two weighings and a blind check by a second person.', 'mos.weigh.ticket', [('state', 'in', ('draft', 'weighed'))], 'tickets not posted', [('state', '=', 'weighed')], []),
            ('buy', 'Laboratory', 'Samples, results against the specification, then a decision by someone else.', 'mos.lab.inspection', [('state', 'in', ('open', 'tested'))], 'waiting for the lab', [('state', '=', 'tested')], []),
            ('buy', 'Goods receipt', 'Stock enters the silo at the weight and quality decided.', 'mos.gate.visit', [('purpose', '=', 'inbound_po'), ('state', 'in', ('weighed', 'cleared')), ('grn_state', '!=', 'confirmed')], 'receipts to confirm', [], []),
            ('buy', 'Supplier invoices', 'Matched to the order, the receipt and the quality adjustment.', 'mos.supplier.invoice', [('state', 'in', ('draft', 'payable'))], 'to match or to pay', [('match_state', '=', 'exception')], []),
            ('buy', 'Payment vouchers', 'Grouped by supplier and approved by amount.', 'mos.payment.voucher', [('state', 'in', ('draft', 'submitted', 'approved', 'partially_paid'))], 'not paid yet', [('state', '=', 'submitted')], []),
            ('buy', 'Payments', 'Cash, transfer, cheque or wallet, with the withholding certificate.', 'mos.payment', [('date', '>=', month)], 'in the last 30 days', [], []),
            # make
            ('make', 'Silos', 'What each silo holds, dip readings against the ledger.', 'mos.silo', [], 'silos', [], []),
            ('make', 'Silo holds', 'A silo being fumigated or cleaned cannot be filled or drawn from.', 'mos.silo.hold', [('state', '=', 'active')], 'silos on hold', [], []),
            ('make', 'Planning suggestions', 'What to make and what to buy, from orders and stock.', 'mos.mrp.suggestion', [('state', '=', 'open')], 'open suggestions', [], []),
            ('make', 'Work orders', 'Which wheat from which silo, on which line, in which shift.', 'mos.work.order', [('state', 'in', ('draft', 'planned', 'running'))], 'to run or running', [], []),
            ('make', 'Milling runs', 'Wheat in, flour and by-products out, extraction against the recipe.', 'mos.milling.run', [('state', '=', 'draft')], 'planned runs', [], []),
            ('make', 'Batches and quality hold', 'Every batch traced to the wheat that made it and the bags that went out.', 'mos.batch', [('state', '=', 'hold')], 'batches on hold', [], []),
            ('make', 'Finished-goods inspection', 'A held batch is tested before it can be packed or sold.', 'mos.batch.inspection', [('state', 'in', ('open', 'tested'))], 'waiting for a decision', [('state', '=', 'tested')], []),
            ('make', 'Packing', 'Bags from a released batch, with the spillage written down.', 'mos.pack.run', [('state', '=', 'draft')], 'planned runs', [], []),
            ('make', 'Stock adjustments', 'A correction needs a reason and, above the threshold, approval.', 'mos.stock.adjustment', [('state', 'in', ('draft', 'pending', 'approved'))], 'not applied yet', [('state', '=', 'pending')], []),
            ('make', 'Cycle counts', 'Blind counting, a recount by someone else, a review.', 'mos.cycle.count', [('state', 'in', ('draft', 'counting', 'review', 'submitted'))], 'in progress', [('state', '=', 'review')], []),
            ('make', 'Transfers between sites', 'In transit until the other site counts what arrived.', 'mos.transfer.order', [('state', 'in', ('draft', 'dispatched'))], 'not received yet', [('state', '=', 'dispatched')], []),
            # sell and collect
            ('sell', 'Customers', 'Approved before the first order, with a credit limit that changes only on a request.', 'res.partner', [('customer_rank', '>', 0), ('mos_customer_state', '=', 'draft')], 'to approve', [], [('customer_rank', '>', 0)]),
            ('sell', 'Quotations', 'Priced above the floor; over the credit limit the general manager decides.', 'sale.order', [('state', 'in', ('draft', 'sent'))], 'not confirmed', [('mos_approval_state', '=', 'pending')], []),
            ('sell', 'Orders to deliver', 'Confirmed, stock reserved, waiting for a truck.', 'sale.order', [('state', '=', 'sale'), ('picking_ids', '!=', False), ('picking_ids.state', 'not in', ('done', 'cancel'))], 'confirmed, not delivered', [], [('state', '=', 'sale')]),
            ('sell', 'Delivery orders', 'Released to the gate with the truck number.', 'mos.delivery.order', [('state', 'in', ('draft', 'released'))], 'not dispatched', [], []),
            ('sell', 'Gate: trucks going out', 'Loaded, weighed, cleared and let out with an exit pass.', 'mos.gate.visit', [('purpose', '=', 'outbound_do'), ('state', 'in', ('expected', 'arrived', 'weighing', 'weighed', 'cleared'))], 'on site or expected', [], [('purpose', '=', 'outbound_do')]),
            ('sell', 'Customer invoices', 'Raised at the loaded weight.', 'mos.customer.invoice', [('balance', '>', 0)], 'not fully paid', [], []),
            ('sell', 'Receipts and cash', 'Money in against invoices or as an advance; the till is counted at close.', 'mos.receipt', [('date', '>=', month)], 'in the last 30 days', [], []),
            ('sell', 'Credit notes', 'Raised for a reason, approved by amount, never edited.', 'mos.credit.note', [('state', 'in', ('draft', 'pending'))], 'not posted', [('state', '=', 'pending')], []),
            ('sell', 'Customer returns', 'Into quarantine first, then restock or scrap, then credit.', 'mos.customer.return', [('state', 'in', ('draft', 'received'))], 'being handled', [], []),
            # control
            ('control', 'Approvals', 'Every request waits for the people the matrix names, with a time limit.', 'mos.approval.request', [('state', '=', 'pending')], 'waiting for a decision', [], [('state', '!=', False)]),
            ('control', 'Exceptions', 'What the rules caught: variances, late transfers, expiring documents.', 'mos.exception', [('state', '=', 'open')], 'open', [('severity', '=', 'high')], []),
            ('control', 'Documents to verify', 'A file is checked by someone other than the person who attached it.', 'mos.attachment', [('verify_state', '=', 'unverified'), ('kind', '!=', False)], 'not verified', [], []),
            ('control', 'Supplier notices', 'What suppliers were told, and what came of it.', 'mos.supplier.notice', [('state', 'in', ('queued', 'failed', 'no_email'))], 'not delivered', [('state', 'in', ('failed', 'no_email'))], []),
            ('control', 'Background reports', 'Large reports built on a schedule.', 'mos.report.job', [('state', 'in', ('queued', 'running'))], 'in the queue', [('state', '=', 'failed')], []),
            ('control', 'Audit trail', 'Every change of consequence, chained so it cannot be edited.', 'mos.audit.event', [('occurred_at', '>=', week)], 'events this week', [], []),
        ]

    @api.model
    def _build(self):
        """Replace this person's stages with fresh ones. A stage is left out when its model is not installed, a field it reads does
        not exist, or the person may not read it."""
        self.sudo().search([('create_uid', '=', self.env.uid)]).unlink()
        made = []
        for number, (lane, name, description, model, open_domain, caption, attention_domain, total_domain) in enumerate(self._stages()):
            if model not in self.env:
                continue
            target = self.env[model]
            try:
                counted = target.search_count(open_domain)
                alert = target.search_count(attention_domain) if attention_domain else 0
                total = target.search_count(total_domain) if total_domain else target.search_count([])
            except (AccessError, ValueError, KeyError):
                continue
            made.append(
                {
                    'sequence': number,
                    'lane': lane,
                    'name': name,
                    'description': description,
                    'caption': caption,
                    'res_model': model,
                    'open_domain': repr(open_domain),
                    'attention_domain': repr(attention_domain),
                    'count': counted,
                    'attention': alert,
                    'total': total,
                }
            )
        return self.create(made)

    @api.model
    def action_open_map(self):
        self._build()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Process map'),
            'res_model': self._name,
            'view_mode': 'kanban',
            'views': [(self.env.ref('mos_process_map.view_mos_process_stage_kanban').id, 'kanban')],
            'domain': [('create_uid', '=', self.env.uid)],
            'target': 'current',
        }

    def _open(self, domain):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.name,
            'res_model': self.res_model,
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': domain,
            'target': 'current',
        }

    def action_open(self):
        """The records the card counts."""
        from ast import literal_eval

        self.ensure_one()
        return self._open(literal_eval(self.open_domain or '[]'))

    def action_open_attention(self):
        from ast import literal_eval

        self.ensure_one()
        return self._open(literal_eval(self.attention_domain or '[]'))
