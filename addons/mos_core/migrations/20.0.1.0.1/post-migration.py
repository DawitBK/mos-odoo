def migrate(cr, version):
    """Sequences that were numbered without date ranges keep counting for ever: that is the 'never' restart policy."""
    cr.execute("UPDATE ir_sequence SET mos_reset = 'never' WHERE use_date_range IS NOT TRUE")
