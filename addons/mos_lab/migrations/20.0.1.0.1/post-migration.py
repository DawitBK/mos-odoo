def migrate(cr, version):
    """Specification versions made before copy=True on the names carry "(copy)" after every parameter and grade name: take it off."""
    for table in ('mos_lab_spec_line', 'mos_lab_grade'):
        cr.execute(rf"UPDATE {table} SET name = regexp_replace(name, '( \(copy\))+$', '') WHERE position('(copy)' in name) > 0")  # noqa: S608 (fixed table names)
