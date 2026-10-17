#!/usr/bin/env node
/**
 * Registers MOS as an OpenID Connect client of the (SaaS) identity provider, and creates the MOS sign-in accounts.
 *
 *   node register-sso-client.mjs --saas-root <dir> --runtime <dir> --redirect <https://host/mos/oidc/callback> [--redirect ...]
 *
 * - writes one idp_clients row ("mos"), with a generated secret kept in <runtime>/secrets.json (key mos.client_secret);
 * - creates one SSO account per MOS demo person (the logins in tools/seed_demo.py), with the shared demo password
 *   (<runtime>/secrets.json, key demo.password), and entitles them to nothing else: MOS is a standalone product, so
 *   the portal tile is not touched.
 * Safe to repeat. The identity provider must be restarted afterwards so it reloads its clients.
 *
 * Why not clientSeed: the company-neutral edition's seed list is generated from the company code, which keeps MOS out
 * of it on purpose (a seeded client with a development secret stops a production SSO from booting).
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const arg = (name) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : undefined;
};
const all = (name) => args.flatMap((a, i) => (a === `--${name}` ? [args[i + 1]] : []));
const saasRoot = arg('saas-root');
const runtime = arg('runtime');
const redirects = all('redirect');
if (!saasRoot || !runtime || !redirects.length) {
  console.error('Usage: register-sso-client.mjs --saas-root <dir> --runtime <dir> --redirect <url> [--redirect <url>]');
  process.exit(2);
}

const ssoBackend = path.join(saasRoot, 'sso-saas', 'backend');
const require = createRequire(path.join(ssoBackend, 'package.json'));
const pg = require('pg');
const { hashPassword } = await import(pathToFileURL(path.join(ssoBackend, 'dist', 'auth', 'password.js')).href);

const secretsFile = path.join(runtime, 'secrets.json');
const secrets = JSON.parse(fs.readFileSync(secretsFile, 'utf8'));
if (!secrets['demo.password']) throw new Error('secrets.json has no demo.password');
if (!secrets['mos.client_secret']) {
  secrets['mos.client_secret'] = crypto.randomBytes(32).toString('base64url');
  fs.writeFileSync(secretsFile, JSON.stringify(secrets, null, 2));
}
const infra = Object.fromEntries(
  fs.readFileSync(path.join(runtime, 'infra.env'), 'utf8').split(/\r?\n/).filter((l) => /^[A-Z_]+=/.test(l)).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]),
);

const PEOPLE = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), 'mos-people.json'), 'utf8'));

const db = new pg.Client({ host: '127.0.0.1', port: Number(infra.POSTGRES_PORT ?? 5440), user: 'postgres', password: infra.POSTGRES_PASSWORD, database: 'sso_saas' });
await db.connect();
try {
  await db.query('begin');
  await db.query(
    `insert into idp_clients (client_id, client_secret, redirect_uris, post_logout_redirect_uris, name, scope, updated_by)
     values ('mos', $1, $2, $3, 'MOS (Odoo)', 'openid profile email', 'register-sso-client')
     on conflict (client_id) do update set redirect_uris = excluded.redirect_uris, post_logout_redirect_uris = excluded.post_logout_redirect_uris,
       scope = excluded.scope, updated_at = now(), updated_by = excluded.updated_by`,
    [secrets['mos.client_secret'], redirects, redirects.map((r) => r.replace(/\/mos\/oidc\/callback$/, '/odoo'))],
  );
  const hash = await hashPassword(secrets['demo.password']);
  let created = 0;
  for (const p of PEOPLE) {
    const r = await db.query(
      `insert into idp_users (email, email_verified, given_name, family_name, password_hash, source, is_active, must_change_password)
       values ($1, true, $2, $3, $4, 'local', true, false) on conflict (email) do nothing`,
      [p.login, p.given, p.family, hash],
    );
    created += r.rowCount;
  }
  await db.query('commit');
  console.log(`client "mos" registered for ${redirects.join(', ')}; ${created} of ${PEOPLE.length} sign-in accounts created`);
} catch (e) {
  await db.query('rollback');
  throw e;
} finally {
  await db.end();
}
