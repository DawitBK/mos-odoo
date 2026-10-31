#!/usr/bin/env node
/**
 * Puts a rebuilt MOS database in the place of the live one, keeping the old one under another name so the change can be undone:
 *
 *   node tools/swap-database.mjs --mos-env <file with MOS_DB_PASSWORD> --candidate mos_birr2 [--live mos] [--keep-as mos_before_birr] [--skip-checks]
 *
 *   1. checks the candidate (company money is birr, the demo accounts exist, the seed steps are recorded) unless --skip-checks
 *   2. stops Odoo, ends the connections to both databases
 *   3. renames the live database to --keep-as and the candidate to the live name
 *   4. starts Odoo again and waits for the sign-in page
 *
 * To undo: run it again with --candidate <the kept name> --keep-as <a new name>. Nothing is deleted.
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const arg = (n, d) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 ? args[i + 1] : d;
};
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const envFile = arg('mos-env');
const candidate = arg('candidate');
const live = arg('live', 'mos');
const keepAs = arg('keep-as', `${live}_before_swap`);
const skipChecks = args.includes('--skip-checks');
if (!envFile || !candidate) {
  console.error('Usage: node tools/swap-database.mjs --mos-env <file> --candidate <database> [--live mos] [--keep-as <name>] [--skip-checks]');
  process.exit(2);
}
for (const name of [candidate, live, keepAs]) if (!/^[a-z0-9_]+$/.test(name)) throw new Error(`Not a plain database name: ${name}`);
const password = fs.readFileSync(envFile, 'utf8').match(/^MOS_DB_PASSWORD=(.+)$/m)?.[1]?.trim();
if (!password) throw new Error(`${envFile} has no MOS_DB_PASSWORD`);

const compose = (...a) => spawnSync('docker', ['compose', '--env-file', envFile, ...a], { cwd: root, encoding: 'utf8', windowsHide: true });
const psql = (db, sql) => {
  const r = compose('exec', '-T', '-e', `PGPASSWORD=${password}`, 'db', 'psql', '-U', 'odoo', '-d', db, '-tAc', sql);
  if (r.status !== 0) throw new Error(`psql on ${db} failed: ${r.stderr.trim()}`);
  return r.stdout.trim();
};
const exists = (db) => psql('postgres', `SELECT 1 FROM pg_database WHERE datname = '${db}'`) === '1';

if (!exists(candidate)) throw new Error(`The candidate database ${candidate} does not exist.`);
if (!exists(live)) throw new Error(`The live database ${live} does not exist.`);
if (exists(keepAs)) throw new Error(`${keepAs} already exists: choose another --keep-as so nothing is overwritten.`);

if (!skipChecks) {
  const currency = psql(candidate, 'SELECT c.name FROM res_company co JOIN res_currency c ON c.id = co.currency_id');
  const steps = Number(psql(candidate, "SELECT count(*) FROM ir_config_parameter WHERE key LIKE 'mos_seed.%'"));
  const demo = Number(psql(candidate, "SELECT count(*) FROM res_users WHERE login LIKE 'mos.%@examplecorp.com' AND active"));
  const sso = psql(candidate, "SELECT value FROM ir_config_parameter WHERE key = 'mos_auth_oidc.issuer'");
  const problems = [];
  if (currency !== 'ETB') problems.push(`the company money is ${currency}, not ETB`);
  if (steps < 40) problems.push(`only ${steps} seed steps are recorded`);
  if (demo < 15) problems.push(`only ${demo} demo accounts exist`);
  if (!sso) problems.push('single sign-on is not set up (no issuer)');
  if (problems.length) {
    console.error(`The candidate is not ready:\n - ${problems.join('\n - ')}`);
    process.exit(1);
  }
  console.log(`candidate ${candidate}: money ${currency}, ${steps} seed steps, ${demo} demo accounts, sign-in ${sso}`);
}

console.log('stopping Odoo...');
compose('stop', 'odoo');
for (const db of [live, candidate]) psql('postgres', `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${db}' AND pid <> pg_backend_pid()`);
psql('postgres', `ALTER DATABASE ${live} RENAME TO ${keepAs}`);
psql('postgres', `ALTER DATABASE ${candidate} RENAME TO ${live}`);
console.log(`${live} is now the rebuilt database; the old one is kept as ${keepAs}.`);

console.log('starting Odoo...');
compose('up', '-d', 'odoo');
const port = fs.readFileSync(envFile, 'utf8').match(/^MOS_ODOO_PORT=(\d+)/m)?.[1] ?? '8069';
let up = false;
for (let i = 0; i < 40 && !up; i++) {
  await new Promise((r) => setTimeout(r, 3000));
  try {
    up = (await fetch(`http://127.0.0.1:${port}/web/login`)).ok;
  } catch {
    // not up yet
  }
}
if (!up) {
  console.error(`Odoo did not answer on port ${port} within two minutes: look at "docker compose logs odoo". The databases are swapped; to undo run this tool with --candidate ${keepAs} --keep-as <a new name>.`);
  process.exit(1);
}
console.log('Odoo answers; the swap is done.');
