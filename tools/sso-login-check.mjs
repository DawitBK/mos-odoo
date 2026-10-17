#!/usr/bin/env node
/**
 * Browser check of the single sign-on login into MOS (Odoo):
 *   node sso-login-check.mjs --mos http://localhost:8069 --email mos.guard@examplecorp.com --password <demo password> --playwright-from <dir with @playwright/test>
 * Opens the Odoo login page, follows "Sign in with SSO", signs in at the identity provider and expects to land in Odoo.
 * Also checks that a person with an identity-provider account but no MOS user is refused.
 */
import path from 'node:path';
import { createRequire } from 'node:module';

const args = process.argv.slice(2);
const arg = (n) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 ? args[i + 1] : undefined;
};
const mos = (arg('mos') ?? 'http://localhost:8069').replace(/\/$/, '');
const email = arg('email');
const password = arg('password');
const from = arg('playwright-from');
const refused = arg('refused-email');
if (!email || !password || !from) {
  console.error('Usage: sso-login-check.mjs --mos <url> --email <e> --password <p> --playwright-from <dir> [--refused-email <e>]');
  process.exit(2);
}
const { chromium } = createRequire(path.join(from, 'package.json'))('@playwright/test');

let failed = 0;
const check = (ok, what) => {
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${what}`);
  if (!ok) failed += 1;
};

async function signIn(who) {
  const browser = await chromium.launch();
  const page = await (await browser.newContext()).newPage();
  try {
    await page.goto(`${mos}/web/login`, { waitUntil: 'domcontentloaded' });
    const button = page.getByRole('link', { name: /sign in with sso/i });
    await button.first().waitFor({ timeout: 30000 }).catch(() => {});
    check((await button.count()) > 0, 'the login page offers "Sign in with SSO"');
    await button.first().click();
    await page.waitForURL(/\/sso\//, { timeout: 45000 });
    await page.waitForLoadState('domcontentloaded');
    if (arg('shots')) await page.screenshot({ path: path.join(arg('shots'), 'sso-step.png') });
    await page.fill('#email', who, { timeout: 15000 });
    await page.fill('#password', password);
    await page.getByRole('button', { name: 'Continue' }).click();
    await page.waitForURL((url) => url.href.startsWith(mos), { timeout: 60000 });
    await page.waitForLoadState('domcontentloaded');
    return { url: page.url(), page, browser };
  } catch (error) {
    if (arg('shots')) await page.screenshot({ path: path.join(arg('shots'), 'sso-fail.png') }).catch(() => {});
    return { url: `error: ${error.message.split('\n')[0]}`, page, browser };
  }
}

const ok = await signIn(email);
check(/\/odoo/.test(ok.url), `signing in as ${email} lands in Odoo (${ok.url})`);
if (/\/odoo/.test(ok.url)) {
  await ok.page.waitForSelector('.o_navbar, .o_web_client', { timeout: 30000 }).catch(() => {});
  check((await ok.page.locator('.o_web_client').count()) > 0, 'the Odoo web client is loaded for that user');
}
await ok.browser.close();

if (refused) {
  const no = await signIn(refused);
  check(/oauth_error=3/.test(no.url) || /\/web\/login/.test(no.url), `${refused} has no MOS account and is refused (${no.url})`);
  await no.browser.close();
}
console.log(failed ? `\n${failed} check(s) failed` : '\nAll checks passed');
process.exit(failed ? 1 : 0);
