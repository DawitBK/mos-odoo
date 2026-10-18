#!/usr/bin/env bash
# Installs the MOS addons in a throwaway database and runs their tests.  Usage: tools/run-tests.sh <env file>
set -euo pipefail
export MSYS_NO_PATHCONV=1
E="${1:?env file}"
# Every module with tests; the install list pulls in their dependencies.
MODULES="mos_core mos_gate mos_lab mos_inbound mos_silo mos_production mos_dashboard mos_approvals mos_payables mos_sales mos_procurement mos_inventory mos_controls mos_flows mos_manufacturing mos_commercial mos_quality mos_gate_plus mos_production_plus mos_finance_plus mos_procurement_plus mos_reports mos_platform_plus mos_planning_plus mos_stock_plus"
INSTALL="mos_dashboard,mos_auth_oidc,mos_approvals,mos_payables,mos_sales,mos_procurement,mos_inventory,mos_controls,mos_flows,mos_manufacturing,mos_commercial,mos_quality,mos_gate_plus,mos_production_plus,mos_finance_plus,mos_procurement_plus,mos_reports,mos_platform_plus,mos_planning_plus,mos_stock_plus"
TAGS=$(for m in $MODULES; do printf '/%s,' "$m"; done)
PW=$(grep MOS_DB_PASSWORD "$E" | cut -d= -f2)
docker compose --env-file "$E" up -d db >/dev/null
docker compose --env-file "$E" exec -T -e PGPASSWORD="$PW" db psql -U odoo -d postgres -c "DROP DATABASE IF EXISTS mos_test" >/dev/null
docker compose --env-file "$E" run --rm -T odoo odoo -d mos_test -i "$INSTALL" --test-tags "${TAGS%,}" --stop-after-init --without-demo=all
