#!/bin/sh
# Seeds the demo database with a full set of records in every state, by driving the same actions the screens use.
#
#   tools/seed_lifecycle.sh <path to the .env file> [part ...]     parts: 0_ledger 1_stock 2_procurement 3_quality 4_finance 5_gate 6_more 7_masters (default: all)
#
# Safe to run again: each step records itself and is skipped when it already ran (see tools/seed_lib.py).
set -e
ENV_FILE="${1:?path to the .env file}"
shift || true
PARTS="${*:-0_ledger 1_stock 2_procurement 3_quality 4_finance 5_gate 6_more 7_masters}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PW="$(grep '^MOS_DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
cd "$HERE/.."
for part in $PARTS; do
  echo "== $part"
  cat "$HERE/seed_lib.py" "$HERE/seed_lifecycle_$part.py" | docker compose --env-file "$ENV_FILE" exec -T odoo odoo shell -d "${MOS_DB:-mos}" --db_host db --db_user odoo --db_password "$PW" --no-http 2>&1 | grep -E '^(done|skip|FAIL|Traceback|\w*Error|  File "<stdin>")'
done
