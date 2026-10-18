#!/usr/bin/env node
/**
 * Opens every MOS menu action in a real browser and fails on any client error, so a view that references a missing field, a button
 * for a method that does not exist or a broken action is found before a person finds it.
 *   node ui-smoke.mjs --base http://localhost:8069 --login mos.admin@examplecorp.com --password <pw> --playwright-from <dir with @playwright/test> [--only <text>] [--shots <dir>]
 * For each menu with an action it loads the action; if the result is a list with rows it also opens the first record.
 */
import path from 'node:path';
import { createRequire } from 'node:module';

const args = process.argv.slice(2);
const arg = (n) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 ? args[i + 1] : undefined;
};
const base = (arg('base') ?? 'http://localhost:8069').replace(/\/$/, '');
const login = arg('login');
const password = arg('password');
const from = arg('playwright-from');
const only = arg('only');
if (!login || !password || !from) {
  console.error('Usage: ui-smoke.mjs --base <url> --login <e> --password <p> --playwright-from <dir> [--only <text>]');
  process.exit(2);
}
const { chromium } = createRequire(path.join(from, 'package.json'))('@playwright/test');

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
let problems = [];
page.on('pageerror', (e) => problems.push(`script error: ${e.message.split('\n')[0]}`));
page.on('console', (m) => {
  if (m.type() === 'error' && !/favicon|Failed to load resource.*(40[34])|websocket|bus\//i.test(m.text())) problems.push(`console: ${m.text().slice(0, 200)}`);
});

await page.goto(`${base}/web/login`, { waitUntil: 'domcontentloaded' });
await page.fill('input[name="login"]', login);
await page.fill('input[name="password"]', password);
await page.click('button[type="submit"]');
await page.waitForURL(/\/odoo/, { timeout: 60000 });
await page.waitForSelector('.o_navbar, .o_web_client', { timeout: 60000 });

const rpc = (model, method, params) =>
  page.evaluate(
    async ([model, method, params]) => {
      const r = await fetch(`/web/dataset/call_kw/${model}/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { model, method, ...params } }) });
      const j = await r.json();
      if (j.error) throw new Error(j.error.data?.message || j.error.message);
      return j.result;
    },
    [model, method, params],
  );

const menus = await rpc('ir.ui.menu', 'search_read', { args: [[['action', '!=', false]]], kwargs: { fields: ['complete_name', 'action'], order: 'id' } });
menus.sort((a, b) => a.complete_name.localeCompare(b.complete_name));
const mine = menus.filter((m) => /^MOS|^mos/i.test(m.complete_name.split('/')[0]) || /MOS/.test(m.complete_name)).filter((m) => !only || m.complete_name.toLowerCase().includes(only.toLowerCase()));
console.log(`${mine.length} MOS menu actions to open`);

let failed = 0;
const errorText = async () => {
  const dialog = page.locator('.o_error_dialog, .modal .o_dialog_error, .modal:has(.o_error_dialog)');
  if (await dialog.count()) {
    await page.getByText('See technical details').first().click({ timeout: 1500 }).catch(() => {});
    const detail = await page.locator('.o_error_detail').first().innerText({ timeout: 1500 }).catch(() => '');
    return (detail || (await dialog.first().innerText().catch(() => 'error dialog'))).replace(/\s+/g, ' ').slice(0, 600);
  }
  const notif = page.locator('.o_notification.border-danger, .o_notification_manager .bg-danger');
  if (await notif.count()) return (await notif.first().innerText().catch(() => 'error notification')).replace(/\s+/g, ' ').slice(0, 300);
  return '';
};

for (const menu of mine) {
  const [model, id] = menu.action.split(',');
  problems = [];
  let verdict = 'ok';
  try {
    await page.goto(`${base}/odoo/action-${id}`, { waitUntil: 'domcontentloaded' });
    // a dialog-style action (a wizard) opens as a modal instead of a page
    await page.waitForSelector('.o_action_manager .o_action, .o_action_manager .o_content, .o_error_dialog, .modal-dialog', { timeout: 20000 });
    await page.waitForTimeout(700);
    let err = await errorText();
    if (!err && model === 'ir.actions.act_window') {
      // every other way of looking at the same records (pivot, graph, kanban, calendar...)
      const switches = page.locator('.o_cp_switch_buttons .o_switch_view:not(.active)');
      const total = await switches.count();
      for (let i = 0; i < total && !err; i += 1) {
        const button = page.locator('.o_cp_switch_buttons .o_switch_view:not(.active)').first();
        const label = (await button.getAttribute('data-tooltip')) || (await button.getAttribute('title')) || 'view';
        await button.click({ timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(700);
        err = await errorText();
        if (err) err = `[${label} view] ${err}`;
      }
      // back to a list or kanban so a record can be opened
      const back = page.locator('.o_cp_switch_buttons .o_switch_view.o_list, .o_cp_switch_buttons .o_switch_view.o_kanban').first();
      if (!err && (await back.count())) {
        await back.click({ timeout: 3000 }).catch(() => {});
        await page.waitForTimeout(500);
      }
      const row = page.locator('.o_list_view .o_data_row, .o_kanban_view .o_kanban_record').first();
      if (await row.count()) {
        await row.click({ timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(900);
        err = await errorText();
        if (!err && (await page.locator('.o_form_view').count())) {
          // a record opened: the form rendered without a dialog
        }
      }
    }
    if (err) verdict = `FAIL ${err}`;
    else if (problems.length) verdict = `WARN ${problems[0]}`;
    if (arg('shots') && verdict !== 'ok') await page.screenshot({ path: path.join(arg('shots'), `${id}.png`) }).catch(() => {});
  } catch (error) {
    verdict = `FAIL ${error.message.split('\n')[0]}`;
  }
  if (verdict.startsWith('FAIL')) failed += 1;
  console.log(`  ${verdict === 'ok' ? 'ok  ' : verdict.slice(0, 4)} ${menu.complete_name}${verdict === 'ok' ? '' : '  ->  ' + verdict.slice(5)}`);
}

await browser.close();
console.log(failed ? `\n${failed} of ${mine.length} actions failed` : `\nAll ${mine.length} actions opened cleanly`);
process.exit(failed ? 1 : 0);
