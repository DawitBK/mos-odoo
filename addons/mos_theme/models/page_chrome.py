"""The client's page layout for every MOS form: a header (icon, title, one line saying what the screen is for, where it sits),
each titled part of the form as its own card, and the buttons of the form in a bar at the foot, as in the 28 mock-ups.

One mechanism for every MOS form, applied when Odoo composes the form from its views, so no view has to repeat it and a new
form gets the layout without anyone remembering to. A form that already carries its own header (class `mos-page-head`) is left alone.
"""

from lxml import etree

from odoo import api, models

# model -> (icon, title, what it is for, section). Models not listed get the model's description and an icon by family.
PAGES = {
    'purchase.order': ('cart', 'Purchase Order', 'Order goods from a supplier, with the quantities, prices and delivery date', 'Procurement'),
    'mos.purchase.request': ('cart', 'Purchase Request', 'Ask procurement for the materials the plant needs', 'Procurement'),
    'mos.gate.visit': ('truck', 'Gate Visit', 'A vehicle at the gate, from arrival to exit', 'Gate'),
    'mos.lab.inspection': ('flask', 'QC Inspection', 'Perform quality inspection and testing for incoming wheat', 'Quality Control'),
    'mos.lab.spec': ('clipboard', 'Specification', 'The limits a delivery must meet', 'Quality Control'),
    'mos.goods.transfer': ('transfer', 'Goods Transfer', 'Transfer goods from one location, bin or warehouse to another', 'Inventory'),
    'mos.stock.card': ('scales', 'Stock Card', 'Every movement of one item in one warehouse, with the running balance', 'Inventory'),
    'mos.transfer.order': ('transfer', 'Transfer Between Sites', 'Move goods from one site to another and receive them there', 'Inventory'),
    'mos.cycle.count': ('clipboard', 'Physical Count', 'Count the stock of a location and compare it with the system', 'Inventory'),
    'mos.stock.adjustment': ('scales', 'Inventory Adjustment', 'Correct the stock after a count or a loss, with the reason', 'Inventory'),
    'mos.supplier.invoice': ('receipt', 'Supplier Invoice', 'Register the invoice of a supplier and match it with the receipt', 'Payables'),
    'mos.payment.voucher': ('money', 'Payment Request', 'Review what is to be paid and approve it', 'Payables'),
    'mos.payment': ('bank', 'Payment Execution', 'Record the payment made to the supplier', 'Payables'),
    'mos.payment.run': ('bank', 'Payment Run', 'Pay several approved vouchers together', 'Payables'),
    'mos.supplier.registration': ('building', 'Supplier Registration', 'Register a new supplier step by step', 'Procurement'),
    'mos.silo': ('sack', 'Silo', 'A storage unit with its capacity and content', 'Silos & Stock'),
    'mos.customer.invoice': ('receipt', 'Customer Invoice', 'Bill a customer for the goods delivered', 'Sales'),
    'mos.delivery.order': ('truck', 'Delivery Order', 'Release goods to a customer', 'Sales'),
    'mos.weigh.ticket': ('scales', 'Weigh Ticket', 'The gross, tare and net weight of a vehicle', 'Gate'),
    'mos.milling.run': ('factory', 'Milling Run', 'Wheat milled into flour and by-products, with the extraction rate', 'Production'),
    'mos.batch': ('cube', 'Production Batch', 'A batch of finished goods with its lot and quality release', 'Production'),
    'mos.rfq': ('doc', 'Request for Quotation', 'Ask suppliers for prices and award the order', 'Procurement'),
    'mos.po.amendment': ('edit', 'Purchase Order Amendment', 'Change an order after it was confirmed, with approval', 'Procurement'),
    'mos.supplier.return': ('arrow-out', 'Supplier Return', 'Send rejected or surplus goods back to the supplier', 'Procurement'),
    'mos.service.receipt': ('check', 'Service Receipt', 'Confirm that a service was delivered', 'Procurement'),
    'mos.purchase.contract': ('doc', 'Purchase Contract', 'An agreement with a supplier over a period', 'Procurement'),
    'mos.site': ('warehouse', 'Site', 'A plant or depot of the company', 'Configuration'),
    'mos.approval.request': ('shield', 'Approval', 'What is waiting for a decision, and who decides', 'Approvals'),
}

MAX_VISIBLE_BUTTONS = 4

# The icon of a model that has no entry above, chosen by its name.
FAMILY_ICONS = (
    ('gate', 'truck'), ('lab', 'flask'), ('quality', 'flask'), ('payable', 'money'), ('payment', 'money'), ('invoice', 'receipt'),
    ('stock', 'cube'), ('inventory', 'cube'), ('count', 'clipboard'), ('silo', 'sack'), ('production', 'factory'), ('milling', 'factory'),
    ('batch', 'cube'), ('sales', 'cart'), ('customer', 'users'), ('supplier', 'building'), ('purchase', 'cart'), ('approval', 'shield'),
    ('transfer', 'transfer'), ('report', 'chart'), ('audit', 'shield'), ('user', 'user'),
)

# The icon of a card, chosen by what its title says.
SECTION_ICONS = (
    (('summary', 'total'), 'clipboard'),
    (('item', 'line', 'detail', 'material', 'parameter', 'result'), 'cube'),
    (('contact', 'person', 'people', 'counter', 'reviewer'), 'user'),
    (('payment', 'bank', 'amount', 'price', 'cost'), 'money'),
    (('date', 'schedule', 'time'), 'calendar'),
    (('sample', 'quality', 'inspection', 'lab', 'certificate'), 'flask'),
    (('address', 'location', 'site', 'warehouse'), 'warehouse'),
    (('history', 'log', 'trail'), 'clock'),
    (('remark', 'note', 'purpose', 'reason', 'comment'), 'edit'),
    (('vehicle', 'truck', 'delivery', 'transport'), 'truck'),
)


def _icon_for(text, table, default):
    text = (text or '').lower()
    for words, icon in table:
        if isinstance(words, str):
            words = (words,)
        if any(word in text for word in words):
            return icon
    return default


# The colour of a status pill, chosen by what the value says. A view that colours a value itself keeps its choice; this only fills what it left out.
TONE_WORDS = (
    ('danger', ('reject', 'cancel', 'fail', 'block', 'overdue', 'void', 'revers', 'lost', 'refus', 'expire', 'error', 'variance', 'declin', 'abandon', 'fraud', 'breach', 'full')),
    ('warning', ('pending', 'wait', 'hold', 'partial', 'review', 'quarantine', 'await', 'due', 'low', 'high', 'unmatched', 'not_matched', 'dispute', 'escalat', 'weigh', 'approval')),
    ('success', ('done', 'approved', 'paid', 'posted', 'confirm', 'match', 'active', 'releas', 'accept', 'closed', 'complete', 'passed', 'ok', 'available', 'receiv', 'sign', 'valid', 'settled', 'exited', 'cleared', 'payable', 'decided', 'applied', 'issued', 'delivered', 'sent', 'verified')),
    ('info', ('draft', 'new', 'open', 'expected', 'plan', 'submit', 'arrived', 'assign', 'running', 'progress', 'ready', 'requested', 'invited', 'candidate', 'voucher')),
)


def _tone_of(value):
    key = str(value).lower()
    for tone, words in TONE_WORDS:
        if any(word in key for word in words):
            return tone
    return None


def _is_status_field(name):
    return name in ('state', 'status') or name.endswith('_state') or name.endswith('_status')


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        # Every MOS record is a full page; a transient model is a dialog, unless it is one of the listed screens. Odoo's own documents get the layout only when they are listed.
        if (view_type or view.type) == 'form' and (self._name in PAGES or (self._name.startswith('mos.') and not self._transient)):
            self._mos_page_chrome(arch)
        if (view_type or view.type) == 'list' and self._name.startswith('mos.'):
            self._mos_status_pills(arch)
        return arch, view

    @api.model
    def _mos_status_pills(self, arch):
        """Every status column of a MOS list is a coloured pill, the same colour for the same meaning in every list."""
        for node in arch.iter('field'):
            name = node.get('name')
            field = self._fields.get(name)
            if field is None or field.type != 'selection' or not _is_status_field(name):
                continue
            if node.get('widget') not in (None, 'badge'):
                continue
            try:
                values = [key for key, _label in field._description_selection(self.env)]
            except Exception:  # a selection that cannot be listed here keeps the view's own look
                continue
            covered = ' '.join(node.get(attr) or '' for attr in node.attrib if attr.startswith('decoration-'))
            missing = {}
            for value in values:
                if "'%s'" % value in covered or '"%s"' % value in covered:
                    continue
                tone = _tone_of(value)
                if tone:
                    missing.setdefault(tone, []).append(value)
            if not missing and node.get('widget') == 'badge':
                continue
            node.set('widget', 'badge')
            for tone, keys in missing.items():
                attr = 'decoration-%s' % tone
                condition = '%s in (%s)' % (name, ', '.join("'%s'" % k for k in keys))
                node.set(attr, '%s or %s' % (node.get(attr), condition) if node.get(attr) else condition)

    @api.model
    def _mos_page_chrome(self, arch):
        sheet = arch.find('.//sheet')
        if sheet is None or arch.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " mos-page-head ")]'):
            return
        if self._name.startswith('mos.'):  # the buttons of Odoo's own documents stay where Odoo's flows expect them
            self._mos_move_buttons_to_the_foot(arch, sheet)
        self._mos_wrap_parts_in_cards(sheet)
        self._mos_plain_groups_to_grids(sheet)
        self._mos_wrap_loose_blocks(sheet)
        sheet.insert(0, self._mos_page_head())

    @api.model
    def _mos_page_head(self):
        icon, title, subtitle, section = PAGES.get(self._name) or (
            _icon_for(self._name, FAMILY_ICONS, 'doc'),
            self._description or self._name,
            '',
            'MOS',
        )
        head = etree.Element('div', {'class': 'mos-page-head'})
        glyph_box = etree.SubElement(head, 'span', {'class': 'mos-page-icon'})
        etree.SubElement(glyph_box, 'i', {'class': 'mos-ico mos-ico-%s' % icon}).text = ''
        text = etree.SubElement(head, 'div', {'class': 'mos-page-title'})
        etree.SubElement(text, 'h2').text = self.env._(title)
        if subtitle:
            etree.SubElement(text, 'p').text = self.env._(subtitle)
        etree.SubElement(head, 'div', {'class': 'mos-page-crumb'}).text = '%s › %s' % (self.env._(section), self.env._(title))
        return head

    @api.model
    def _mos_wrap_parts_in_cards(self, sheet):
        """Each <separator string="..."/> directly in the sheet starts a card that holds everything up to the next one."""
        children = list(sheet)
        starts = [i for i, node in enumerate(children) if node.tag == 'separator' and node.get('string')]
        for index in reversed(range(len(starts))):
            start = starts[index]
            end = starts[index + 1] if index + 1 < len(starts) else len(children)
            separator = children[start]
            card = etree.Element('div', {'class': 'mos-section'})
            for attribute in ('invisible', 'groups'):
                if separator.get(attribute):
                    card.set(attribute, separator.get(attribute))
            title = etree.SubElement(card, 'div', {'class': 'mos-section-title'})
            etree.SubElement(title, 'i', {'class': 'mos-ico mos-ico-%s' % _icon_for(separator.get('string'), SECTION_ICONS, 'doc')}).text = ''
            etree.SubElement(title, 'span').text = separator.get('string')
            body = children[start + 1:end]
            sheet.remove(separator)
            sheet.insert(start, card)
            for node in body:
                card.append(node)  # moves the node out of the sheet into the card

    WIDE_TYPES = ('one2many', 'many2many', 'text', 'html', 'binary')

    @api.model
    def _mos_group_to_grid(self, group):
        """A plain group (columns of label and value) becomes the labelled grid of the hand-built pages: the label above the value.

        Only a group made of fields, labels and sub-headings is converted; one holding buttons, widgets or anything else keeps Odoo's layout.
        """
        leaves = []  # (kind, item) in reading order; a group's heading comes before its fields

        def walk(node):
            for child in node:
                if not isinstance(child.tag, str):
                    continue
                if child.tag == 'group':
                    if child.get('string'):
                        leaves.append(('sub', child.get('string')))
                    if not walk(child):
                        return False
                elif child.tag == 'separator':
                    if child.get('string'):
                        leaves.append(('sub', child.get('string')))
                elif child.tag == 'field':
                    leaves.append(('field', child))
                elif child.tag == 'label':
                    continue  # a label of a field is made again from the field
                else:
                    return False
            return True

        if group.get('string'):
            leaves.append(('sub', group.get('string')))
        if not walk(group) or not any(kind == 'field' for kind, _ in leaves):
            return None
        grid = etree.Element('div', {'class': 'mos-fieldgrid mos-fieldgrid-auto'})
        for attribute in ('invisible', 'groups'):
            if group.get(attribute):
                grid.set(attribute, group.get(attribute))
        for kind, item in leaves:
            if kind == 'sub':
                etree.SubElement(grid, 'div', {'class': 'mos-fieldgrid-wide mos-subhead'}).text = item
                continue
            name = item.get('name')
            field = self._fields.get(name)
            wide = (field is not None and field.type in self.WIDE_TYPES) or item.get('nolabel') == '1' or item.get('colspan') == '2'
            cell = etree.SubElement(grid, 'div', {'class': 'mos-fieldgrid-wide' if wide else ''})
            if item.get('invisible'):
                cell.set('invisible', item.get('invisible'))
            if item.get('groups'):
                cell.set('groups', item.get('groups'))
            if item.get('nolabel') != '1':
                label = etree.SubElement(cell, 'label', {'for': name})
                if item.get('string'):
                    label.set('string', item.get('string'))
                    del item.attrib['string']
            cell.append(item)
        return grid

    @api.model
    def _mos_plain_groups_to_grids(self, sheet):
        """Every plain group of the form: in a card, in a tab, or loose in the sheet."""
        for group in list(sheet.iter('group')):
            parent = group.getparent()
            if parent is None or parent.tag == 'group':
                continue  # inner groups are taken with their outer group
            if parent.tag not in ('sheet', 'div', 'page', 'form'):
                continue
            if any(a.tag == 'field' for a in group.iterancestors()):
                continue  # the sub-view of a list or form field keeps its layout
            grid = self._mos_group_to_grid(group)
            if grid is not None:
                parent.replace(group, grid)

    @api.model
    def _mos_wrap_loose_blocks(self, sheet):
        """A group or notebook that sits directly in the sheet, outside every card, becomes a card of its own (its string is the card title)."""
        for node in list(sheet):
            converted = node.tag == 'div' and 'mos-fieldgrid-auto' in (node.get('class') or '')
            if node.tag not in ('group', 'notebook') and not converted:
                continue
            if 'mos-' in (node.get('class') or '') and not converted:
                continue
            card = etree.Element('div', {'class': 'mos-section mos-section-loose'})
            for attribute in ('invisible', 'groups'):
                if node.get(attribute):
                    card.set(attribute, node.get(attribute))
            title = node.get('string') if node.tag == 'group' else None
            if title:
                head = etree.SubElement(card, 'div', {'class': 'mos-section-title'})
                etree.SubElement(head, 'i', {'class': 'mos-ico mos-ico-%s' % _icon_for(title, SECTION_ICONS, 'doc')}).text = ''
                etree.SubElement(head, 'span').text = title
                del node.attrib['string']
            index = list(sheet).index(node)
            sheet.remove(node)
            sheet.insert(index, card)
            card.append(node)

    @api.model
    def _mos_move_buttons_to_the_foot(self, arch, sheet):
        header = arch.find('.//header')
        if header is None:
            return
        buttons = [node for node in header if node.tag == 'button']
        if not buttons:
            return
        primary = [b for b in buttons if 'btn-primary' in (b.get('class') or '')]
        bar = etree.SubElement(sheet, 'div', {'class': 'mos-action-bar'})
        secondary = [b for b in buttons if b not in primary]
        # Up to MAX_VISIBLE_BUTTONS stay in the bar; the rest wait in a "More actions" menu so a busy screen (a gate visit has a dozen) stays readable.
        visible, extra = (secondary[:MAX_VISIBLE_BUTTONS], secondary[MAX_VISIBLE_BUTTONS:]) if len(secondary) > MAX_VISIBLE_BUTTONS + 1 else (secondary, [])
        for button in visible:
            header.remove(button)
            bar.append(button)
        if extra:
            more = etree.SubElement(bar, 'details', {'class': 'mos-bar-more'})
            etree.SubElement(more, 'summary', {'class': 'btn btn-secondary'}).text = self.env._('More actions')
            menu = etree.SubElement(more, 'div', {'class': 'mos-more-menu'})
            for button in extra:
                header.remove(button)
                menu.append(button)
        for button in primary:
            header.remove(button)
            bar.append(button)
