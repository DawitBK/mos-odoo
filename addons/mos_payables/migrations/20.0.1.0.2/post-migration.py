def migrate(cr, version):
    """Invoices that billed one item in their own columns: the item becomes the first line of the invoice, with its match."""
    for column in ('product_id', 'qty', 'unit_price'):
        cr.execute(f'ALTER TABLE mos_supplier_invoice ALTER COLUMN {column} DROP NOT NULL')  # noqa: S608 (fixed column names)
    cr.execute(
        """
        INSERT INTO mos_supplier_invoice_line
               (invoice_id, sequence, product_id, qty, unit_price, net_amount, total_amount,
                ordered_qty, received_qty, ordered_price, qty_variance, price_variance_pct, match_state,
                create_uid, write_uid, create_date, write_date)
        SELECT i.id, 10, i.product_id, i.qty, i.unit_price, i.net_amount, i.gross_amount,
               i.ordered_qty, i.received_qty, i.ordered_price, i.qty_variance, i.price_variance_pct,
               CASE WHEN i.match_state IN ('matched', 'exception') THEN i.match_state END,
               i.create_uid, i.write_uid, i.create_date, i.write_date
          FROM mos_supplier_invoice i
         WHERE i.product_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM mos_supplier_invoice_line l WHERE l.invoice_id = i.id)
        """
    )
