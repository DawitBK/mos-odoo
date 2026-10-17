#!/usr/bin/env bash
# Installs the MOS addons in a throwaway database and runs their tests.  Usage: tools/run-tests.sh <env file>
set -euo pipefail
export MSYS_NO_PATHCONV=1
E="${1:?env file}"
PW=$(grep MOS_DB_PASSWORD "$E" | cut -d= -f2)
docker compose --env-file "$E" up -d db >/dev/null
docker compose --env-file "$E" exec -T -e PGPASSWORD="$PW" db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS mos_test" >/dev/null
docker compose --env-file "$E" run --rm -T odoo odoo -d mos_test -i mos_inbound,mos_auth_oidc \
  --test-tags /mos_core,/mos_gate,/mos_lab,/mos_inbound --stop-after-init --without-demo=all
