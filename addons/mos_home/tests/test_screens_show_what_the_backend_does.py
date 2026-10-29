import inspect
import re

from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

# Action methods that are reached some other way than a button of their own, with how. Anything not listed here must have a button.
REACHED_ANOTHER_WAY = {
    ('mos.cycle.count', 'action_request_changes'): 'called by the review dialog (mos_counts wizard)',
    ('mos.gate.visit', 'action_entry_reject'): 'called by the delivery-entry dialog',
    ('mos.gate.visit', 'action_prepare_grn_safely'): 'the receipt button calls it through action_prepare_receipt',
    ('mos.gate.visit.share', 'action_prepare'): 'called for each share when the visit prepares its receipts',
    ('mos.pv.draft', 'action_reverse'): 'called when the receipt it came from is reversed',
    ('mos.rfq', 'action_award'): 'called by the award dialog',
    ('mos.overview', 'action_open_figures'): 'opened by the "All figures" button of the home screen',
}


@tagged('post_install', '-at_install')
class TestScreensShowWhatTheBackendDoes(TransactionCase):
    """The owner's rule: the screens represent everything the backend can do. A rule that needs a person to act must give that person a button."""

    def _buttons(self):
        names = set()
        views = self.env['ir.ui.view'].with_context(active_test=False).search([('type', 'in', ('form', 'list', 'kanban')), ('model', 'like', 'mos.')])
        for view in views:
            try:
                arch = etree.fromstring((view.arch_db or '<x/>').encode())
            except etree.XMLSyntaxError:
                continue
            for button in arch.iter('button'):
                if button.get('name'):
                    names.add(button.get('name'))
                names.update(re.findall(r"mos_method'\s*:\s*'(\w+)'", button.get('context') or ''))
            # a button an inheriting view renames: <attribute name="name">new_method</attribute>
            names.update(node.text.strip() for node in arch.iter('attribute') if node.get('name') == 'name' and node.text)
        # an action offered from the Actions menu or a menu entry is a server action that calls the method
        for server_action in self.env['ir.actions.server'].search([('state', '=', 'code')]):
            names.update(re.findall(r'\.(action_\w+|button_\w+)\(', server_action.code or ''))
        return names

    def test_every_action_method_has_a_button(self):
        buttons = self._buttons()
        missing = []
        for model in self.env['ir.model'].search([('model', 'like', 'mos.')]).mapped('model'):
            if model not in self.env:
                continue
            klass = type(self.env[model])
            for name in {n for k in klass.__mro__ for n in vars(k) if n.startswith(('action_', 'button_'))}:
                owner = next((vars(k)[name] for k in klass.__mro__ if name in vars(k)), None)
                module = getattr(inspect.getmodule(owner), '__name__', '') if callable(owner) else ''
                if not module.startswith('odoo.addons.mos_'):
                    continue
                if name not in buttons and (model, name) not in REACHED_ANOTHER_WAY:
                    missing.append('%s.%s' % (model, name))
        self.assertFalse(missing, 'These actions exist in the backend but no screen offers them: %s' % ', '.join(sorted(missing)))

    def test_the_listed_exceptions_are_still_real(self):
        """An entry that is no longer needed (the method is gone, or now has its own button) is removed from the list."""
        buttons = self._buttons()
        for (model, name), why in REACHED_ANOTHER_WAY.items():
            self.assertIn(model, self.env, model)
            self.assertTrue(hasattr(self.env[model], name), '%s.%s no longer exists (%s)' % (model, name, why))
            self.assertNotIn(name, buttons, '%s.%s now has a button: remove it from the list' % (model, name))
