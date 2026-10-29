from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

# Fields that are plumbing, not information: a person gains nothing from seeing them.
PLUMBING = {
    'id', 'display_name', 'create_uid', 'create_date', 'write_uid', 'write_date', 'active', 'sequence', 'site_id', 'res_model', 'res_id',
    'prev_hash', 'hash', 'token', 'filename', 'mos_approval_request_id', 'mos_my_step_id', 'mos_approval_comment', 'mos_attachment_count',
    'mos_unverified_count', 'has_message', 'activity_plans_ids',
}
PLUMBING_PREFIXES = ('message_', 'activity_', 'my_activity', 'website_message')

# Stored fields of a model with its own form that no screen shows, and why that is right. Anything else hidden fails the test.
EXCEPTIONS = {
    'mos.cycle.count': {
        'review_required': 'decides whether the review step is shown; the person sees the step, not the flag',
        'hide_book': 'hides the book quantity during a blind count; it is the screen behaviour itself',
        'item_count': 'the totals strip already counts the items for each unit',
    },
    'mos.flow.setting': {'name': 'the constant title of the single settings record'},
    'mos.gate.visit': {'ticket_stage': 'decides which weighing button is offered; the buttons are the display of it'},
    'mos.lab.spec': {'rule_count': 'the rules are listed on the specification lines themselves'},
    'mos.payment.voucher': {'approved_bank_digest': 'a hash that detects a bank change after approval; the person sees the refusal it causes'},
    'mos.supplier.bank.change': {'new_account_enc': 'the encrypted account number; the screen shows the masked number'},
    'mos.supplier.invoice': {
        'ref_norm': 'the supplier reference in a normalised form, for duplicate detection; the reference itself is shown',
        'approval_gate': 'decides whether the send-for-approval button is offered',
        'product_id': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'qty': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'unit_price': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'ordered_qty': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'received_qty': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'ordered_price': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'qty_variance': 'shorthand of a one-item invoice, computed from the item lines that are shown',
        'price_variance_pct': 'shorthand of a one-item invoice, computed from the item lines that are shown',
    },
}


@tagged('post_install', '-at_install')
class TestFieldsAreOnAScreen(TransactionCase):
    """The owner's rule: a stored fact is visible somewhere. Technical fields are skipped by pattern; the rest must be on a form or list, or be listed here."""

    def _shown(self):
        shown = {}

        def walk(node, model):
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                if child.tag == 'field' and child.get('name'):
                    name = child.get('name')
                    hidden = child.get('invisible') in ('1', 'True', 'true') or child.get('column_invisible') in ('1', 'True', 'true')
                    if not hidden:
                        shown.setdefault(model, set()).add(name)
                    field = self.env[model]._fields.get(name) if model in self.env else None
                    comodel = getattr(field, 'comodel_name', None)
                    walk(child, comodel or model)
                else:
                    walk(child, model)

        views = self.env['ir.ui.view'].with_context(active_test=False).search([('inherit_id', '=', False), ('type', 'in', ('form', 'list', 'kanban')), ('model', 'like', 'mos.')])
        forms = set()
        for view in views:
            if view.model not in self.env:
                continue
            if view.type == 'form':
                forms.add(view.model)
            arch = view._get_combined_arch()
            walk(etree.fromstring(arch.encode()) if isinstance(arch, str) else arch, view.model)
        return shown, forms

    def test_every_stored_field_is_on_a_screen_or_has_a_reason(self):
        shown, forms = self._shown()
        parents = set()
        for model in self.env.registry.values():
            for field in model._fields.values():
                if field.type == 'one2many' and getattr(field, 'inverse_name', None):
                    parents.add((field.comodel_name, field.inverse_name))
        hidden = {}
        for model in sorted(forms):  # a model without a form of its own gets a generated one that shows every field
            Model = self.env[model]
            if Model._transient or not Model._auto:
                continue
            for name, field in Model._fields.items():
                if name in PLUMBING or name.startswith(PLUMBING_PREFIXES) or name.endswith('_filename') or (model, name) in parents:
                    continue
                if not (field.store or field.compute) or name in shown.get(model, set()):
                    continue
                if name in EXCEPTIONS.get(model, {}):
                    continue
                hidden.setdefault(model, []).append(name)
        self.assertFalse(hidden, 'Stored fields on no screen (show them, or list them in EXCEPTIONS with a reason):\n' + '\n'.join('%s: %s' % (m, ' '.join(f)) for m, f in hidden.items()))

    def test_the_exceptions_are_still_hidden(self):
        """An exception that is now shown, or whose field is gone, is removed from the list."""
        shown, _forms = self._shown()
        for model, fields in EXCEPTIONS.items():
            for name in fields:
                self.assertIn(name, self.env[model]._fields, '%s.%s no longer exists' % (model, name))
                self.assertNotIn(name, shown.get(model, set()), '%s.%s is on a screen now: remove it from EXCEPTIONS' % (model, name))
