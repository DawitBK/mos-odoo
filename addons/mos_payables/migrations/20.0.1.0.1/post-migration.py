def migrate(cr, version):
    """Payments made before the allocation table existed: spread each over the invoices of its voucher, oldest first, as a payment
    made without a table is now."""
    cr.execute(
        """
        SELECT p.id, p.voucher_id, p.amount
          FROM mos_payment p
         WHERE NOT EXISTS (SELECT 1 FROM mos_payment_line l WHERE l.payment_id = p.id)
         ORDER BY p.voucher_id, p.id
        """
    )
    left_on_line = {}  # voucher line id -> what is still to be paid on it, as the allocations made so far leave it
    for payment_id, voucher_id, amount in cr.fetchall():
        cr.execute('SELECT id, net FROM mos_payment_voucher_line WHERE voucher_id = %s ORDER BY id', (voucher_id,))
        left = amount
        rows = []
        for line_id, net in cr.fetchall():
            balance = left_on_line.setdefault(line_id, net or 0.0)
            take = round(min(balance, left), 2)
            if take > 0.005:
                rows.append((line_id, take, net - balance + 0.0, balance))
                left_on_line[line_id] = balance - take
                left -= take
        if rows and left > 0.005:
            line_id, take, previous, balance = rows[-1]
            rows[-1] = (line_id, round(take + left, 2), previous, balance)
        for line_id, take, previous, balance in rows:
            cr.execute(
                'INSERT INTO mos_payment_line (payment_id, voucher_line_id, include, amount, previously_paid, balance_due, create_uid, write_uid, create_date, write_date) '
                'VALUES (%s, %s, TRUE, %s, %s, %s, 1, 1, now() AT TIME ZONE \'UTC\', now() AT TIME ZONE \'UTC\')',
                (payment_id, line_id, take, previous, balance),
            )
