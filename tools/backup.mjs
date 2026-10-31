#!/usr/bin/env node
/**
 * Backs up MOS and proves the backup can be restored (ADR-0022: nightly full backup, the attachment store, a copy elsewhere, a restore test).
 *
 *   node tools/backup.mjs backup --mos-env <file with MOS_DB_PASSWORD> --out <dir> [--db mos] [--keep 14] [--copy-to <dir>]
 *   node tools/backup.mjs check  --mos-env <file> --out <dir> [--db mos]        restores the newest backup into a scratch database and checks it
 *
 * A backup is one folder, mos-YYYYMMDD-HHMMSS, holding: database.dump (pg_dump custom format), filestore/ (the attached files),
 * and manifest.json (sizes, SHA-256 of the dump, row counts and the head of the audit hash chain). The manifest is also the external
 * copy of the chain head: a restored database whose chain does not end at that hash has been altered.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const [command, ...args] = process.argv.slice(2);
const arg = (n, d) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 ? args[i + 1] : d;
};
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const envFile = arg('mos-env');
const out = arg('out');
const db = arg('db', 'mos');
const keep = Number(arg('keep', '14'));
const copyTo = arg('copy-to');
const SCRATCH = 'mos_restore_check';

function die(message) {
  console.error(message);
  process.exit(1);
}
if (!['backup', 'check'].includes(command) || !envFile || !out) {
  die('Usage: node tools/backup.mjs <backup|check> --mos-env <file> --out <dir> [--db mos] [--keep 14] [--copy-to <dir>]');
}
const password = fs.readFileSync(envFile, 'utf8').match(/^MOS_DB_PASSWORD=(.+)$/m)?.[1]?.trim();
if (!password) die(`${envFile} has no MOS_DB_PASSWORD.`);

function compose(extra, opts = {}) {
  const r = spawnSync('docker', ['compose', '--env-file', envFile, ...extra], { cwd: root, windowsHide: true, maxBuffer: 1 << 28, ...opts });
  if (r.status !== 0 && !opts.allowFail) die(`docker compose ${extra.slice(0, 4).join(' ')} failed:\n${r.stderr?.toString().slice(-1500)}`);
  return r;
}
const inDb = (...cmd) => compose(['exec', '-T', '-e', `PGPASSWORD=${password}`, 'db', ...cmd]);
const sql = (database, query) => inDb('psql', '-U', 'odoo', '-d', database, '-tAc', query).stdout.toString().trim();
const sha256 = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

function chainFacts(database) {
  const [count, head] = sql(database, "SELECT count(*) || '|' || coalesce((SELECT hash FROM mos_audit_event ORDER BY id DESC LIMIT 1), '') FROM mos_audit_event").split('|');
  return { events: Number(count), head };
}

function countFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).reduce((n, e) => n + (e.isDirectory() ? countFiles(path.join(dir, e.name)) : 1), 0);
}

function folders() {
  return fs.existsSync(out) ? fs.readdirSync(out).filter((f) => /^mos-\d{8}-\d{6}$/.test(f)).sort() : [];
}

if (command === 'backup') {
  const now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
  const dir = path.join(out, `mos-${stamp}`);
  fs.mkdirSync(dir, { recursive: true });

  // The dump first, so the chain head read next is at least as new as the dump (a later event does not break the check, an earlier one would).
  const dump = compose(['exec', '-T', '-e', `PGPASSWORD=${password}`, 'db', 'pg_dump', '-U', 'odoo', '-d', db, '-Fc']);
  fs.writeFileSync(path.join(dir, 'database.dump'), dump.stdout);
  const facts = chainFacts(db);

  // Odoo keeps the attached files under its data directory, which is not the same place on every image: ask the container where.
  const found = compose(['exec', '-T', 'odoo', 'sh', '-c', `find /var/lib/odoo -type d -path '*/filestore/${db}' | head -1`], { allowFail: true }).stdout.toString().trim();
  const attached = Number(sql(db, 'SELECT count(DISTINCT store_fname) FROM ir_attachment'));
  const copied = found ? compose(['cp', `odoo:${found}`, path.join(dir, 'filestore')], { allowFail: true }) : { status: 1 };
  const hasFiles = copied.status === 0;
  if (attached > 0 && !hasFiles) die(`The database refers to ${attached} stored files but the file store was not found in the odoo container: this backup is incomplete.`);
  const stored = hasFiles ? countFiles(path.join(dir, 'filestore')) : 0;
  if (stored < attached) die(`The database refers to ${attached} stored files but only ${stored} were copied: this backup is incomplete.`);

  const manifest = {
    database: db,
    createdAt: now.toISOString(),
    dumpBytes: fs.statSync(path.join(dir, 'database.dump')).size,
    dumpSha256: sha256(path.join(dir, 'database.dump')),
    filestore: hasFiles,
    attachmentsInDatabase: attached,
    filestoreFiles: stored,
    auditEvents: facts.events,
    auditHead: facts.head,
    installedModules: Number(sql(db, "SELECT count(*) FROM ir_module_module WHERE state = 'installed'")),
  };
  fs.writeFileSync(path.join(dir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  console.log(`backup written: ${dir} (${(manifest.dumpBytes / 1e6).toFixed(1)} MB, ${manifest.auditEvents} audit events, ${stored} stored files copied)`);

  if (copyTo) {
    fs.mkdirSync(copyTo, { recursive: true });
    fs.cpSync(dir, path.join(copyTo, path.basename(dir)), { recursive: true });
    console.log(`second copy: ${path.join(copyTo, path.basename(dir))}`);
  }
  for (const old of folders().slice(0, Math.max(0, folders().length - keep))) {
    fs.rmSync(path.join(out, old), { recursive: true, force: true });
    console.log(`removed old backup ${old}`);
  }
} else {
  const newest = folders().at(-1);
  if (!newest) die(`No backup in ${out}.`);
  const dir = path.join(out, newest);
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  const problems = [];

  if (sha256(path.join(dir, 'database.dump')) !== manifest.dumpSha256) problems.push('the dump file does not match its recorded SHA-256');

  inDb('psql', '-U', 'odoo', '-d', 'postgres', '-c', `DROP DATABASE IF EXISTS ${SCRATCH}`);
  inDb('psql', '-U', 'odoo', '-d', 'postgres', '-c', `CREATE DATABASE ${SCRATCH}`);
  try {
    const restore = compose(['exec', '-T', '-e', `PGPASSWORD=${password}`, 'db', 'pg_restore', '-U', 'odoo', '-d', SCRATCH, '--no-owner'], { input: fs.readFileSync(path.join(dir, 'database.dump')), allowFail: true });
    if (restore.status !== 0) problems.push(`pg_restore reported: ${restore.stderr.toString().slice(-400)}`);
    const facts = chainFacts(SCRATCH);
    // The dump was taken before the head was read, so the restored chain may be shorter, never longer, and must be a prefix of what was recorded.
    if (facts.events > manifest.auditEvents) problems.push('the restored database has more audit events than the manifest recorded');
    const modules = Number(sql(SCRATCH, "SELECT count(*) FROM ir_module_module WHERE state = 'installed'"));
    if (modules !== manifest.installedModules) problems.push(`installed modules: ${modules}, the manifest says ${manifest.installedModules}`);
    const recorded = sql(SCRATCH, `SELECT count(*) FROM mos_audit_event WHERE hash = '${manifest.auditHead.replace(/[^a-f0-9]/g, '')}'`);
    if (manifest.auditHead && recorded !== '1') problems.push('the audit chain head recorded in the manifest is not in the restored database');
    const verify = compose(['exec', '-T', 'odoo', 'sh', '-c', `echo "print('CHAIN', env['mos.audit.event'].verify_chain())" | odoo shell -d ${SCRATCH} --db_host db --db_user odoo --db_password ${password} --no-http 2>&1 | grep CHAIN`], { allowFail: true });
    const line = verify.stdout.toString().trim();
    if (!line.endsWith('None')) problems.push(`audit chain verification: ${line || 'no answer'}`);
    console.log(`restore test of ${newest}: ${facts.events} audit events, ${modules} modules, chain ${line || 'unverified'}`);
  } finally {
    inDb('psql', '-U', 'odoo', '-d', 'postgres', '-c', `DROP DATABASE IF EXISTS ${SCRATCH}`);
  }
  if (problems.length) die(`RESTORE TEST FAILED for ${newest}:\n - ${problems.join('\n - ')}`);
  console.log('restore test passed');
}
