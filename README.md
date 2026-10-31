# mos-odoo

MOS (Manufacturing Operations Suite) as addons on Odoo 20 Community. **Private repository.**

This exists because the owner decided to build MOS on Odoo even though the fit-gap recommended against it
(`MOS/docs/3-design/ODOO-FIT-GAP.md`, proposed ADR-0026: only 31% of the requirements are met by Odoo as shipped,
55% are custom work, and almost none of the community modules MOS needs exist on 20.0 yet). So these addons build,
on Odoo, the parts Odoo does not have. Where a requirement needs a community module that is not on 20.0, this repo
writes the small version it needs rather than waiting.

| Addon | What it adds | Requirements it cites |
|---|---|---|
| `mos_approvals` | Approval matrix by amount, site and role, segregation of duties, delegation, escalation, approvals inbox (FR-PLT-023..032, 054) | FR-PLT: 8 |
| `mos_areas` | Each role sees the menus of the areas it works in, not every screen of the mill | - |
| `mos_auth_oidc` | Sign in through an OpenID Connect identity provider; users must already exist (FR-PLT-010, 011); signs out with it | FR-PLT: 2 |
| `mos_commercial` | Customer approval and credit-limit changes, price floors and discount limits, approved price lists, credit notes, customer returns, proof of delivery, statements | FR-SAL: 7 |
| `mos_controls` | Period close and lock dates, backdating windows, date sanity checks, the process exceptions queue and global search | FR-PLT: 4 |
| `mos_core` | Sites, scoping, reason codes, hash-chained audit log and posting rules for MOS | FR-MD: 1; FR-PLT: 9 |
| `mos_counts` | Blind counting, a printable count sheet, import of counts from a spreadsheet, a recount by a different person when a variance is large, and a review that can ask for changes (FR-INV-009) | FR-INV: 1 |
| `mos_dashboard` | The flour mill at a glance: trucks, inspections, tonnes received, silos, milling and extraction | FR-PLT: 1 |
| `mos_dashboards` | The approvals dashboard (total, pending, approved, rejected, average approval time with a six-month trend, each figure opening its list) and the mill overview as cards | FR-PLT: 1; FR-RPT: 1 |
| `mos_deliveries` | The supplier delivery entry screen (FR-PRC-023): delivery note, supplier and items taken from the purchase order, declared quantities with totals per unit, approve to inspection or reject with a reason | FR-PRC: 1; FR-RPT: 1 |
| `mos_documents` | Typed attachment slots with a Verify status on purchase requests and orders, deliveries, supplier invoices and payment vouchers; optional approval of every supplier invoice (FR-AP-020); optional deposit slip and verified invoice rules | FR-AP: 1; FR-PLT: 1 |
| `mos_finance_plus` | Self-billing, supplier statements, GRNI reconciliation, VAT and withholding registers, journal export for the external accounts, payment runs | FR-AP: 6 |
| `mos_flows` | Flow B (packaged goods into quarantine), flow C (lab-led, gate off), automatic receipts, pre-flight checks, receipt exceptions, payables drafted from receipts | FR-INB: 6; FR-QC: 1 |
| `mos_gate` | Gate visits, weigh tickets, watch list, shifts, exit pass, incidents, stored tare, material gate passes and throughput report | FR-GATE: 17 |
| `mos_gate_multi` | One truck delivering against several purchase orders: intermediate weighings, allocation of the net weight, a receipt per document | FR-GATE: 1 |
| `mos_gate_plus` | Gate posts and weighbridges, plate search and expected arrivals, tolerance rules, clearance log with supervisor override, service visits, paper mode with back-entry, gate registers | FR-GATE: 12 |
| `mos_home` | The first screen of MOS: what needs the person today, the day in figures, quick actions, silos, trucks at the gate and the latest milling runs | - |
| `mos_inbound` | Inbound raw-material flow: gate, weighing, inspection and a system-prepared goods receipt | FR-INB: 2 |
| `mos_inventory` | Stock adjustments with approval, cycle counts, quarantine and blocked stock, reorder report, archive rules, labels | FR-GATE: 1; FR-INV: 7; FR-MD: 1; FR-PLT: 1 |
| `mos_lab` | Versioned specifications, inspections, rule-based deductions, grading, sampling plans, retention samples, certificates of analysis and lab reports | FR-QC: 11 |
| `mos_manufacturing` | Batches and traceability, finished-goods release, packing, fortification dosing, work centres, downtime, cost settlement, production and planner reports | FR-MFG: 8; FR-QC: 1 |
| `mos_master_plus` | Unique tax numbers and duplicate warnings, individual suppliers, item conversions that never change once used, quality concessions through the approval matrix, heavy reports as background jobs | FR-MD: 4; FR-QC: 1; FR-RPT: 1 |
| `mos_notify` | E-mail to suppliers: arrival acknowledged, rejection note with results, receipt with deductions, payment advice; queued until a mail server exists | FR-INB: 1 |
| `mos_operations_ui` | The dialogs and buttons that let people do from the browser what the models already allow: weigh a truck, verify the weights, re-weigh, resolve or dismiss an exception, void a cheque, split a lot, order from a request, call off a contract and more | FR-GATE: 1 |
| `mos_payables` | Supplier invoices with three-way match, debit notes, payment vouchers, payments, cheque books, ageing (FR-AP-001..019) | FR-AP: 9 |
| `mos_planning_plus` | Versioned routings on the work order, MRP-lite suggestions with pegging, labour and overhead costing with variances, soft stock reservations for sales orders, purchase contracts with call-offs | FR-MFG: 3; FR-PRC: 1; FR-SAL: 1 |
| `mos_platform_plus` | Role dashboards, access review, site switching, identity-group role mapping, session limits, stock position and balance reports, period-close checklist, exception notifications, Ethiopian dates | FR-INV: 4; FR-PLT: 8; FR-RPT: 2 |
| `mos_print` | The branded PDFs the requirements list and the first modules left out (FR-PLT-049): goods receipt note, supplier receipt with the deduction breakdown, self-billed voucher, debit note, withholding certificate, rejection note, customer invoice, customer receipt, delivery note | FR-PLT: 1 |
| `mos_process_map` | The whole mill on one board, from the supplier to the payment, from the wheat to the flour and from the order to the cash: every stage with what is waiting in it, and a click into its list; and the document trail of an order, every linked document in the order the work happened | - |
| `mos_procurement` | Purchase requests, supplier approval and documents, bank-change control, sourcing and receipt policies, PO amendments, supplier returns, GRN reassignment | FR-PRC: 9 |
| `mos_procurement_plus` | Quotation comparison and award, purchase order closure, bag tare, service receipts, supplier registration by invitation, procurement reports | FR-PRC: 6 |
| `mos_production` | Milling runs: wheat drawn from silos, flour and bran produced, extraction rate (FR-MFG-005..015) | FR-MFG: 2; FR-PLT: 2 |
| `mos_production_plus` | Versioned recipes with grist ratios, work orders, silo dip readings against the ledger, fumigation and treatment holds | FR-INV: 2; FR-MFG: 3 |
| `mos_quality` | Test methods and parameters, QC policy with waived and skip-lot inspection, calculated parameters, instruments and calibration, sample labels, lab queue | FR-QC: 4 |
| `mos_reports` | Weigh ticket, exit pass with QR, material gate pass, certificate of analysis, payment voucher | - |
| `mos_request_forms` | The purchase requisition as the client draws it (FR-PRC-007): request date, priority, requester ID, remarks, live available stock and quantity to purchase per item, specification and notes; PART 1 / PART 2 layout on the request and the purchase order | FR-PRC: 1 |
| `mos_sales` | Sales orders with credit control and approval, delivery orders through the gate, invoices, receipts, cash sessions, ageing (FR-SAL-001..018) | FR-SAL: 8 |
| `mos_silo` | Grain silos with capacity, live level from the stock ledger and fill status (FR-INV-019) | FR-INV: 1 |
| `mos_stock_cards` | The client's Bin card and Stock card screens: filters, summary cards, stock by bin and the transaction history with a running balance, all read from the one stock ledger; printable | FR-INV: 3 |
| `mos_stock_plus` | Lots with batch numbering, supplier lot, expiry and category attributes, lot split and expiry list, transfers between sites with stock in transit, supplier service categories | FR-INV: 3; FR-MD: 3 |
| `mos_supplier_portal` | A supplier signs in to see its own orders, promise delivery dates, answer requests for quotation, submit invoices and read its statement | - |
| `mos_theme` | The blue and navy look of the client screens: colours, status pills, section headers, summary cards and tables, for every MOS screen | - |

42 addons. The table is generated from each addon's manifest and the requirement ids its code cites; the status of every FRD requirement
(built, partial, gap) is in `MOS/docs/3-design/IMPLEMENTATION-STATUS.md`. The order of the work is in `docs/ROADMAP.md`.

**Known limit (fit-gap section 4):** Odoo connects to its database as the owner, so "not even an administrator can
change a posted row, enforced by database grants" cannot be kept. The addons enforce it in the application and add a
trigger on the audit table; the daily chain check and an external copy of the latest hash are what make tampering visible.

## Run it

```powershell
# 1. a password file outside the repository
New-Item -ItemType Directory -Force C:\Users\$env:USERNAME\runtime\mos | Out-Null
"MOS_DB_PASSWORD=$([guid]::NewGuid().ToString('N'))" | Set-Content C:\Users\$env:USERNAME\runtime\mos\mos.env

# 2. database, install the addons, and run their tests
docker compose --env-file C:\Users\$env:USERNAME\runtime\mos\mos.env up -d db
docker compose --env-file C:\Users\$env:USERNAME\runtime\mos\mos.env run --rm odoo odoo -d mos_test -i mos_inbound,mos_auth_oidc --test-tags /mos_core,/mos_gate,/mos_lab,/mos_inbound --stop-after-init --without-demo=all

# 3. the real database, demo data, and the web server on http://127.0.0.1:8069
docker compose --env-file C:\Users\$env:USERNAME\runtime\mos\mos.env run --rm odoo odoo -d mos -i mos_inbound,mos_auth_oidc --stop-after-init --without-demo=all
docker compose --env-file C:\Users\$env:USERNAME\runtime\mos\mos.env up -d odoo
```

Sign-in through the identity provider: `tools/register-sso-client.mjs` registers the client and the accounts; set the
four `mos_auth_oidc.*` system parameters (issuer, client id, client secret, optional redirect URI) and enable the
"SSO" provider under Settings, Users, OAuth providers.

## Backup and restore test

```powershell
node tools/backup.mjs backup --mos-env $env --out C:\Users\$env:USERNAME\runtime\mos\backups --keep 14 [--copy-to <a second place>]
node tools/backup.mjs check  --mos-env $env --out C:\Users\$env:USERNAME\runtime\mos\backups
```

`backup` writes one folder with the database dump, a copy of the attached files and a manifest (sizes, the dump's SHA-256 and the head of the audit hash chain,
which is the external copy of the chain that the known limit above relies on). It refuses to finish when the database refers to files it could not copy.
`check` restores the newest backup into a scratch database, compares it with the manifest, runs the chain verification on it and drops it again.
Schedule `backup` nightly (Windows Task Scheduler or cron) and `check` weekly, and keep a second copy off the machine.

## After you change a model, a view or a data file

The `addons` folder is mounted into the running container, so new code is loaded at the next start, but the database is not
changed by that. A new field, view or data file needs an upgrade of its addon, otherwise every page that reads the changed
table fails (a missing column made every sign-in answer "Forbidden" on 2026-10-24). Back up, upgrade, restart:

```powershell
$env = "C:\Users\$env:USERNAME\runtime\mos\mos.env"
docker compose --env-file $env run --rm odoo odoo -d mos -u <addon>[,<addon>] --stop-after-init --no-http
docker compose --env-file $env restart odoo
```

Check it with `tools/sso-login-check.mjs` (a real sign-in through the public address), and read the container log for errors
after the restart.

## Demo data in every state

`tools/seed_lifecycle.sh <env file> [part ...]` fills the demo database with records in every state of their life, by calling the same actions the
screens call (so every rule, approval and separation of duties applies to the demo data as to real work). Six parts, run in order, each step recorded
under the system parameter `mos_seed.<step>` so a second run skips what is already there and a failed step leaves nothing behind:

| Part | What it adds |
| --- | --- |
| `1_stock` | reason codes, a second site, lots with expiry and attributes, silo holds, approval delegations, background reports, stock adjustments, transfers between sites and cycle counts in every state |
| `2_procurement` | suppliers in every standing, bank changes, invitations, requests, requests for quotation, a contract, quotes, amendments, supplier returns, receipt reassignments, a service order with receipts, the supplier portal and the notices owed to suppliers |
| `3_quality` | deduction bands, inspections at every stage, concessions in every state, decision revisions, certificates of analysis, a lost sample, work orders in every state with labour |
| `4_finance` | quotations, orders waiting for approval or over the credit limit, confirmed orders not yet delivered, trucks waiting at the gate and on the scale, customer returns, vouchers waiting or refused, invoices being entered, a payment reversal, planning suggestions |
| `5_gate` | a contractor with tools, a visitor, transfer trucks, a cancelled arrival, one truck carrying two orders weighed in steps |
| `6_more` | lot attributes, finished-goods inspections and packing from the released batches, stock status changes, payment runs, journal exports, guard shifts |

Run it after `tools/provision.mjs` and the older `seed_*.py` scripts. `ui-smoke.mjs` ends by listing the screens that still open with no records.

## Checking the screens

The unit tests prove the code, not that a person can use the screen, so three browser and role checks sit beside them (they need Playwright;
`DAWIT-LABS/saas/node_modules` has it):

```powershell
node tools/ui-smoke.mjs --base http://localhost:8069 --login mos.admin@examplecorp.com --password <pw> --playwright-from <dir>   # opens every menu action
node tools/ui-journey.mjs --base http://localhost:8069 --password <pw> --playwright-from <dir>                                    # a wheat delivery through six people
```

`tools/role_check.py` does the same for every role in seconds, inside the Odoo shell (it takes `PEOPLE`, a JSON list of logins, defined first). The journey receives one whole order of local wheat each
time it runs; `tools/seed_journey_orders.py` (run in the Odoo shell) tops the open orders up again.

Licence: LGPL-3.0, as Odoo.
