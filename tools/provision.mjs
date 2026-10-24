#!/usr/bin/env node
/**
 * Builds (or rebuilds) the MOS database from nothing and leaves it ready to use:
 *
 *   node provision.mjs --mos-env <file with MOS_DB_PASSWORD> --runtime <dir with secrets.json> \
 *        --mos-origin https://mos.example.com --issuer https://portal.example.com/sso [--password <demo password>] [--reset] \
 *        [--visitor-email <public sandbox login>] [--visitor-name <its name>]
 *
 *   1. (with --reset) drops the database "mos"
 *   2. installs the MOS addons
 *   3. loads the flour-mill demo data (tools/seed_demo.py)
 *   4. points sign-in at the identity provider (system parameters, never in code) and sets the public address
 *   5. sets the demo password on the MOS demo users, but not on the platform administrator
 *   6. maps the roles the identity provider's console offers for MOS to MOS roles and lets it create accounts (tools/seed_idp_roles.py)
 *   7. (with --visitor-email) gives the public sandbox login a MOS administrator account, single sign-on only (tools/seed_visitor.py)
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const arg = (n) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 ? args[i + 1] : undefined;
};
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const envFile = arg('mos-env');
const runtime = arg('runtime');
const mosOrigin = (arg('mos-origin') ?? '').replace(/\/+$/, '');
const issuer = (arg('issuer') ?? '').replace(/\/+$/, '');
const password = arg('password');
const reset = args.includes('--reset');
const visitorEmail = arg('visitor-email');
const visitorName = arg('visitor-name');
if (!envFile || !runtime || !mosOrigin || !issuer) {
  console.error('Usage: provision.mjs --mos-env <file> --runtime <dir> --mos-origin <url> --issuer <url> [--password <p>] [--reset]');
  process.exit(2);
}
const dbPassword = fs.readFileSync(envFile, 'utf8').match(/^MOS_DB_PASSWORD=(.*)$/m)?.[1]?.trim();
const secrets = JSON.parse(fs.readFileSync(path.join(runtime, 'secrets.json'), 'utf8'));
const clientSecret = secrets['mos.client_secret'];
if (!dbPassword || !clientSecret) throw new Error('MOS_DB_PASSWORD or mos.client_secret is missing');

const compose = (...a) => spawnSync('docker', ['compose', '--env-file', envFile, ...a], { cwd: root, encoding: 'utf8', input: undefined });
const odooShell = (script) =>
  spawnSync('docker', ['compose', '--env-file', envFile, 'exec', '-T', 'odoo', 'odoo', 'shell', '-d', 'mos', '--db_host', 'db', '--db_user', 'odoo', '--db_password', dbPassword, '--no-http'], {
    cwd: root,
    encoding: 'utf8',
    input: script,
    maxBuffer: 1 << 26,
  });
const must = (r, what) => {
  if (r.status !== 0) {
    console.error(`${what} failed:\n${(r.stdout ?? '').slice(-1500)}\n${(r.stderr ?? '').slice(-2500)}`);
    process.exit(1);
  }
};

must(compose('up', '-d', 'db'), 'start database');
if (reset) {
  compose('stop', 'odoo');
  const drop = spawnSync('docker', ['compose', '--env-file', envFile, 'exec', '-T', '-e', `PGPASSWORD=${dbPassword}`, 'db', 'psql', '-U', 'odoo', '-d', 'postgres', '-c', 'DROP DATABASE IF EXISTS mos'], { cwd: root, encoding: 'utf8' });
  must(drop, 'drop database');
}
console.log('installing the addons...');
must(compose('run', '--rm', '-T', 'odoo', 'odoo', '-d', 'mos', '-i', 'mos_dashboard,mos_auth_oidc,mos_payables,mos_sales,mos_procurement,mos_inventory,mos_controls,mos_flows,mos_manufacturing,mos_commercial,mos_quality,mos_gate_plus,mos_production_plus,mos_finance_plus,mos_procurement_plus,mos_reports,mos_platform_plus,mos_planning_plus,mos_stock_plus,mos_gate_multi,mos_notify,mos_master_plus,mos_supplier_portal', '--stop-after-init', '--without-demo=all'), 'install');
must(compose('up', '-d', 'odoo'), 'start odoo');
await new Promise((r) => setTimeout(r, 8000));

console.log('loading the flour-mill data...');
const seed = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_demo.py'), 'utf8'));
must(seed, 'seed');
console.log((seed.stdout ?? '').trim().split('\n').slice(-1)[0]);
console.log('loading the money side (invoices, payments, sales)...');
const business = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_business.py'), 'utf8'));
must(business, 'business seed');
console.log((business.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading procurement (approved suppliers, policies, requests)...');
const procurement = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_procurement.py'), 'utf8'));
must(procurement, 'procurement seed');
console.log((procurement.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading gate, lab, inventory and control data...');
const controls = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_controls.py'), 'utf8'));
must(controls, 'controls seed');
console.log((controls.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading production (work centres, packing, batches, downtime)...');
const manufacturing = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_manufacturing.py'), 'utf8'));
must(manufacturing, 'manufacturing seed');
console.log((manufacturing.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading commercial controls (customers, credit notes, returns, delivery proofs)...');
const commercial = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_commercial.py'), 'utf8'));
must(commercial, 'commercial seed');
console.log((commercial.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading quality (methods, instruments, QC policy)...');
const quality = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_quality.py'), 'utf8'));
must(quality, 'quality seed');
console.log((quality.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading gate operations (posts, weighbridge, paper slips)...');
const gate = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_gate.py'), 'utf8'));
must(gate, 'gate seed');
console.log((gate.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading production planning (recipes, dips, work orders)...');
const planning = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_planning.py'), 'utf8'));
must(planning, 'planning seed');
console.log((planning.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading finance extras (account mapping, payment run, journal export)...');
const finance = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_finance.py'), 'utf8'));
must(finance, 'finance seed');
console.log((finance.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('loading sourcing, platform and planning extras (RFQ, bag types, contract, routing, suggestions)...');
const extras = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_extras.py'), 'utf8'));
must(extras, 'extras seed');
console.log((extras.stdout ?? '').trim().split('\n').slice(-1)[0]);

console.log('configuring sign-in and passwords...');
const configure = `
p = env['ir.config_parameter'].sudo()
p.set_str('mos_auth_oidc.issuer', ${JSON.stringify(issuer)})
p.set_str('mos_auth_oidc.client_id', 'mos')
p.set_str('mos_auth_oidc.client_secret', ${JSON.stringify(clientSecret)})
p.set_str('mos_auth_oidc.redirect_uri', ${JSON.stringify(mosOrigin + '/mos/oidc/callback')})
p.set_str('web.base.url', ${JSON.stringify(mosOrigin)})
env.ref('mos_auth_oidc.provider_mos_sso').sudo().enabled = True
env['res.lang']._activate_lang('am_ET')
env['ir.module.module']._load_module_terms(env['ir.module.module'].search([('name', '=like', 'mos_%'), ('state', '=', 'installed')]).mapped('name'), ['am_ET'], overwrite=True)
${password ? `
for u in env['res.users'].sudo().search([('login', '=like', 'mos.%@examplecorp.com')]):
    u.password = ${JSON.stringify(password)}
` : ''}
env.cr.commit()
print('configured')
`;
must(odooShell(configure), 'configure');
console.log("mapping the identity provider's roles to MOS roles...");
const roleMap = odooShell(fs.readFileSync(path.join(root, 'tools', 'seed_idp_roles.py'), 'utf8'));
must(roleMap, 'identity role mapping');
console.log((roleMap.stdout ?? '').trim().split('\n').slice(-1)[0]);
if (visitorEmail) {
  console.log('giving the public sandbox login its administrator account...');
  const header = `VISITOR_EMAIL = ${JSON.stringify(visitorEmail)}\nVISITOR_NAME = ${JSON.stringify(visitorName ?? 'Demo Administrator')}\n`;
  const visitor = odooShell(header + fs.readFileSync(path.join(root, 'tools', 'seed_visitor.py'), 'utf8'));
  must(visitor, 'visitor account');
  console.log((visitor.stdout ?? '').trim().split('\n').slice(-1)[0]);
}
console.log('MOS is ready at', mosOrigin);
