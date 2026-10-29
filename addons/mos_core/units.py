"""Quantities of different units are never added together: a screen shows one total for each unit."""

from markupsafe import Markup, escape


def totals_html(totals, sign=False):
    """``{unit name: quantity}`` as "<b>1,200.0</b> kg + <b>40.0</b> bag". A unit whose total is zero is left out; nothing at all shows 0."""
    fmt = '{:+,.1f}' if sign else '{:,.1f}'
    parts = [Markup('<strong>{}</strong> {}').format(fmt.format(qty), escape(unit)) for unit, qty in sorted(totals.items()) if abs(qty) > 0.00005]
    return Markup(' &nbsp;+&nbsp; ').join(parts) if parts else Markup('<strong>0</strong>')
