from . import models


def post_init_hook(env):
    """A new installation values stock at the moving weighted average from the start (FR-INV-010)."""
    env["product.category"]._mos_set_average_costing()
