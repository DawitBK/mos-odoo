# mos-odoo

MOS (Manufacturing Operations Suite) as addons on Odoo 20 Community. **Private repository.**

This exists because the owner decided to build MOS on Odoo even though the fit-gap recommended against it
(`MOS/docs/3-design/ODOO-FIT-GAP.md`, proposed ADR-0026: only 31% of the requirements are met by Odoo as shipped,
55% are custom work, and almost none of the community modules MOS needs exist on 20.0 yet). So these addons build,
on Odoo, the parts Odoo does not have. Where a requirement needs a community module that is not on 20.0, this repo
writes the small version it needs rather than waiting.

| Addon | What it adds | Requirements |
|---|---|---|
| `mos_core` | Sites and per-site scoping, reason codes, **hash-chained append-only audit log** (daily verification), posting rules (a posted record cannot be changed or deleted), gap-free sequences | FR-PLT-002..004, 012, 021, 022, 041, 046, FR-MD-009 |
| `mos_gate` | Gate visits (one open visit per vehicle), **weigh tickets**: first and second weighing, blind verification by a second person, superseding re-weigh with a reason, gap-free numbers | FR-GATE-001..014, 030 |
| `mos_lab` | Versioned, approved **specifications**, inspections frozen to the spec in force, results that are never overwritten, automatic pass/fail, usage decision by a different person, quality deduction, concession needs a lab manager | FR-QC-001..003, 008..014 |
| `mos_inbound` | **Flow A**: the receipt is prepared by the system at the payable quantity and confirmed by a storekeeper (not the buyer); exit clearance checks the decision and the receipt | FR-INB-001..007, FR-PRC-013 |
| `mos_auth_oidc` | Sign in through the identity provider: authorization code flow with PKCE; users must already exist | FR-PLT-010, 011 |

**Not built yet:** the approvals and segregation-of-duties matrix (`mos_approvals`), payables (self-billing, PV, cheque
books), production, sales gate hand-off, exception queue, reports. See the fit-gap for the full list; the order is in
`docs/ROADMAP.md`.

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

Licence: LGPL-3.0, as Odoo.
