#!/usr/bin/env node
/**
 * A person does a real piece of work in a real browser: a truck arrives with wheat, the guard weighs it twice and a second person
 * verifies both weights without seeing them. It proves the screens exist and work, not just the code behind them.
 *   node ui-journey.mjs --base http://localhost:8069 --password <pw> --playwright-from <dir> [--guard mos.guard@...] [--verifier mos.verifier@...] [--shots <dir>]
 */
import path from 'node:path';
import { createRequire } from 'node:module';

const args = process.argv.slice(2);
const arg = (n, d) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 ? args[i + 1] : d;
};
const base = arg('base', 'http://localhost:8069').replace(/\/$/, '');
const password = arg('password');
const from = arg('playwright-from');
const guardLogin = arg('guard', 'mos.guard@examplecorp.com');
const verifierLogin = arg('verifier', 'mos.verifier@examplecorp.com');
const shots = arg('shots');
if (!password || !from) {
  console.error('Usage: ui-journey.mjs --base <url> --password <p> --playwright-from <dir>');
  process.exit(2);
}
const { chromium } = createRequire(path.join(from, 'package.json'))('@playwright/test');

let failed = 0;
const check = (ok, what) => {
  console.log(`  ${ok ? 'ok  ' : 'FAIL'} ${what}`);
  if (!ok) failed += 1;
};

const browser = await chromium.launch();
async function session(login) {
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`${base}/web/login`);
  await page.fill('input[name="login"]', login);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/odoo/, { timeout: 60000 });
  await page.waitForSelector('.o_navbar');
  return { page, errors };
}
const shot = (page, name) => (shots ? page.screenshot({ path: path.join(shots, `${name}.png`) }).catch(() => {}) : Promise.resolve());
const settle = (page, ms = 800) => page.waitForTimeout(ms);
const dialogError = async (page) => {
  const d = page.locator('.o_error_dialog, .modal:has(.o_error_dialog)');
  return (await d.count()) ? (await d.first().innerText().catch(() => 'error dialog')).replace(/\s+/g, ' ').slice(0, 300) : '';
};
async function menuAction(page, name) {
  const id = await page.evaluate(async (name) => {
    const r = await fetch('/web/dataset/call_kw/ir.ui.menu/mos_visible_actions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { model: 'ir.ui.menu', method: 'mos_visible_actions', args: [], kwargs: {} } }) });
    const j = await r.json();
    return (j.result || []).find((m) => m.complete_name === name)?.action?.split(',')[1];
  }, name);
  if (!id) throw new Error(`no menu ${name}`);
  await page.goto(`${base}/odoo/action-${id}`);
  await page.waitForSelector('.o_action_manager .o_control_panel', { timeout: 20000 });
  await settle(page);
}
async function pickFirst(page, field) {
  const input = page.locator(`.o_field_widget[name="${field}"] input`);
  await input.click();
  await input.fill('');
  await settle(page, 600);
  const item = page.locator('.o-autocomplete--dropdown-item').first();
  await item.waitFor({ timeout: 8000 });
  const label = (await item.innerText()).trim();
  await item.click();
  return label;
}
const plate = `UI-${Date.now().toString().slice(-6)}`;

// 1. The guard registers the truck
console.log('The guard registers a truck and weighs it');
const guard = await session(guardLogin);
try {
  await menuAction(guard.page, 'MOS/Gate/Visits');
  await guard.page.getByRole('button', { name: /^new$/i }).click();
  await guard.page.waitForSelector('.o_form_view');
  await guard.page.locator('.o_field_widget[name="plate"] input').fill(plate);
  const siteInput = guard.page.locator('.o_field_widget[name="site_id"] input');
  if (!(await siteInput.inputValue())) await pickFirst(guard.page, 'site_id');
  await pickFirst(guard.page, 'purchase_id');
  await guard.page.locator('.o_form_button_save').click();
  await settle(guard.page);
  check(!(await dialogError(guard.page)), 'the visit is saved (a guard shift is open, the order is picked)');
  await shot(guard.page, 'journey-1-visit');

  await guard.page.locator('button[name="action_start_weighing"]').click();
  await settle(guard.page);
  check(!(await dialogError(guard.page)), 'weighing started');
  const firstButton = guard.page.getByRole('button', { name: 'Record the first weighing' });
  await firstButton.waitFor({ timeout: 8000 });
  check(true, 'the screen offers "Record the first weighing"');
  await firstButton.click();
  await guard.page.waitForSelector('.modal .o_form_view');
  await guard.page.locator('.modal .o_field_widget[name="weight"] input').fill('50000');
  await shot(guard.page, 'journey-2-first');
  await guard.page.locator('.modal footer button.btn-primary').click();
  await settle(guard.page, 1200);
  check(!(await dialogError(guard.page)), 'the first weight (50,000 kg) is recorded');

  const secondButton = guard.page.getByRole('button', { name: 'Record the second weighing' });
  await secondButton.waitFor({ timeout: 8000 });
  await secondButton.click();
  await guard.page.waitForSelector('.modal .o_form_view');
  const hint = await guard.page.locator('.modal .alert').innerText();
  check(/empty truck/i.test(hint), `the dialog says what to weigh next ("${hint.trim()}")`);
  await guard.page.locator('.modal .o_field_widget[name="weight"] input').fill('15000');
  await guard.page.locator('.modal footer button.btn-primary').click();
  await settle(guard.page, 1200);
  check(!(await dialogError(guard.page)), 'the second weight (15,000 kg) is recorded');
  const verifyButton = guard.page.getByRole('button', { name: /Verify the weights/ });
  await verifyButton.waitFor({ timeout: 8000 });
  check(true, 'the ticket now waits for verification');
  await shot(guard.page, 'journey-3-waiting');
} catch (error) {
  check(false, `the guard's part: ${error.message.split('\n')[0]}`);
  await shot(guard.page, 'journey-guard-failure');
}

// 2. A second person verifies without seeing the weights
console.log('A second person verifies the weights blind');
const verifier = await session(verifierLogin);
try {
  await menuAction(verifier.page, 'MOS/Gate/Visits');
  await verifier.page.locator('.o_searchview input').fill(plate);
  await verifier.page.keyboard.press('Enter');
  await settle(verifier.page, 1000);
  await verifier.page.locator('.o_data_row').first().click();
  await verifier.page.waitForSelector('.o_form_view');
  const button = verifier.page.getByRole('button', { name: /Verify the weights/ });
  await button.waitFor({ timeout: 8000 });
  await button.click();
  await verifier.page.waitForSelector('.modal .o_form_view');
  const text = (await verifier.page.locator('.modal').innerText()).replace(/\s+/g, ' ');
  check(!/50,?000|15,?000/.test(text), 'the verification dialog does not show the recorded weights');
  await verifier.page.locator('.modal .o_field_widget[name="weight_gross"] input').fill('50000');
  await verifier.page.locator('.modal .o_field_widget[name="weight_tare"] input').fill('14000');
  await verifier.page.locator('.modal footer button.btn-primary').click();
  await settle(verifier.page, 1200);
  const wrong = await dialogError(verifier.page);
  check(Boolean(wrong), 'a wrong reading is refused');
  await verifier.page.keyboard.press('Escape');
  await settle(verifier.page, 500);
  await verifier.page.reload();
  await verifier.page.waitForSelector('.o_form_view');
  await verifier.page.getByRole('button', { name: /Verify the weights/ }).click();
  await verifier.page.waitForSelector('.modal .o_form_view');
  await verifier.page.locator('.modal .o_field_widget[name="weight_gross"] input').fill('50000');
  await verifier.page.locator('.modal .o_field_widget[name="weight_tare"] input').fill('15000');
  await verifier.page.locator('.modal footer button.btn-primary').click();
  await settle(verifier.page, 1500);
  check(!(await dialogError(verifier.page)), 'the right reading is accepted');
  await verifier.page.reload();
  await verifier.page.waitForSelector('.o_form_view');
  const net = await verifier.page.locator('.o_field_widget[name="net_weight"]').first().innerText();
  check(/35,?000/.test(net), `the net weight is ${net.trim()} kg`);
  check((await verifier.page.locator('.o_statusbar_status .o_arrow_button_current').innerText()).toLowerCase().includes('weighed'), 'the visit is now "Weighed"');
  await shot(verifier.page, 'journey-4-done');
} catch (error) {
  check(false, `the verifier's part: ${error.message.split('\n')[0]}`);
  await shot(verifier.page, 'journey-verifier-failure');
}

// Helpers for the rest of the delivery
const rpc = (page, model, method, args = [], kwargs = {}) =>
  page.evaluate(
    async ([model, method, args, kwargs]) => {
      const r = await fetch(`/web/dataset/call_kw/${model}/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { model, method, args, kwargs } }) });
      const j = await r.json();
      if (j.error) throw new Error(j.error.data?.message || j.error.message);
      return j.result;
    },
    [model, method, args, kwargs],
  );
async function openVisit(page) {
  await menuAction(page, 'MOS/Gate/Visits');
  await page.locator('.o_searchview input').fill(plate);
  await page.keyboard.press('Enter');
  await settle(page, 1000);
  await page.locator('.o_data_row').first().click();
  await page.waitForSelector('.o_form_view');
  await settle(page, 600);
}
async function press(page, name, what) {
  const button = page.getByRole('button', { name });
  await button.first().waitFor({ timeout: 8000 });
  await button.first().click();
  await settle(page, 1200);
  const error = await dialogError(page);
  check(!error, `${what}${error ? ': ' + error : ''}`);
  return !error;
}

// 3. The lab analyst opens the inspection and enters every result
console.log('The lab tests the wheat and a manager decides');
const analyst = await session('mos.analyst@examplecorp.com');
try {
  await openVisit(analyst.page);
  await press(analyst.page, 'Open inspection', 'the inspection is opened from the visit');
  await menuAction(analyst.page, 'MOS/Lab/Inspections');
  await analyst.page.locator('.o_searchview input').fill(plate);
  await analyst.page.keyboard.press('Enter');
  await settle(analyst.page, 1000);
  await analyst.page.locator('.o_data_row').first().click();
  await analyst.page.waitForSelector('.o_form_view');
  const specId = await analyst.page.evaluate(() => null);
  const url = analyst.page.url();
  const inspectionId = Number(url.split('/').pop());
  const [inspection] = await rpc(analyst.page, 'mos.lab.inspection', 'read', [[inspectionId], ['spec_id']]);
  const params = await rpc(analyst.page, 'mos.lab.spec.line', 'search_read', [[['spec_id', '=', inspection.spec_id[0]]]], { fields: ['name', 'min_value', 'max_value'] });
  check(params.length > 0, `the specification has ${params.length} parameter(s) to measure`);
  for (let i = 0; i < params.length; i += 1) {
    await analyst.page.locator('.o_field_widget[name="result_ids"] .o_field_x2many_list_row_add a').click();
    await settle(analyst.page, 500);
    const lineInput = analyst.page.locator('.o_selected_row .o_field_widget[name="line_id"] input');
    await lineInput.click();
    await settle(analyst.page, 600);
    const options = analyst.page.locator('.o-autocomplete--dropdown-item');
    await options.nth(Math.min(i, (await options.count()) - 1)).click();
    const chosen = (await lineInput.inputValue()).trim();
    const spec = params.find((p) => p.name === chosen) ?? params[i];
    const mid = ((spec.min_value || 0) + (spec.max_value || spec.min_value || 10)) / 2;
    await analyst.page.locator('.o_selected_row .o_field_widget[name="value"] input').fill(String(mid));
    await analyst.page.locator('.o_form_sheet h1, .o_form_sheet').first().click({ position: { x: 5, y: 5 } });
    await settle(analyst.page, 500);
  }
  await analyst.page.locator('.o_form_button_save').click();
  await settle(analyst.page, 1000);
  check(!(await dialogError(analyst.page)), `the ${params.length} result(s) are saved`);
  await press(analyst.page, 'Submit results', 'the results are submitted');
  await shot(analyst.page, 'journey-5-results');
} catch (error) {
  check(false, `the analyst's part: ${error.message.split('\n')[0]}`);
  await shot(analyst.page, 'journey-analyst-failure');
}

const labManager = await session('mos.labmanager@examplecorp.com');
try {
  await menuAction(labManager.page, 'MOS/Lab/Inspections');
  await labManager.page.locator('.o_searchview input').fill(plate);
  await labManager.page.keyboard.press('Enter');
  await settle(labManager.page, 1000);
  await labManager.page.locator('.o_data_row').first().click();
  await labManager.page.waitForSelector('.o_form_view');
  await labManager.page.getByRole('button', { name: 'Usage decision' }).click();
  await labManager.page.waitForSelector('.modal .o_form_view');
  await shot(labManager.page, 'journey-6-decision');
  await labManager.page.locator('.modal footer button.btn-primary').click();
  await settle(labManager.page, 1500);
  const refused = await dialogError(labManager.page);
  check(!refused, `the manager's decision is recorded${refused ? ': ' + refused : ''}`);
} catch (error) {
  check(false, `the lab manager's part: ${error.message.split('\n')[0]}`);
  await shot(labManager.page, 'journey-labmanager-failure');
}

// 4. The receipt is prepared and confirmed by the storekeeper, then the truck leaves
console.log('The goods are received and the truck leaves');
const store = await session('mos.storekeeper@examplecorp.com');
try {
  await openVisit(store.page);
  if (await store.page.getByRole('button', { name: 'Prepare goods receipt' }).count()) await press(store.page, 'Prepare goods receipt', 'the system prepares the receipt at the payable quantity');
  await press(store.page, 'Confirm goods receipt', 'the storekeeper confirms the receipt');
  await shot(store.page, 'journey-7-received');
} catch (error) {
  check(false, `the storekeeper's part: ${error.message.split('\n')[0]}`);
  await shot(store.page, 'journey-store-failure');
}
const guard2 = await session(guardLogin);
try {
  await openVisit(guard2.page);
  await press(guard2.page, 'Clear exit', 'the guard clears the exit');
  await press(guard2.page, 'Record exit', 'the truck is recorded leaving');
  const state = await guard2.page.locator('.o_statusbar_status .o_arrow_button_current').innerText();
  check(/exited/i.test(state), `the visit ends as "${state.trim()}"`);
  await shot(guard2.page, 'journey-8-exited');
} catch (error) {
  check(false, `the guard's second part: ${error.message.split('\n')[0]}`);
  await shot(guard2.page, 'journey-guard2-failure');
}

await browser.close();
console.log(failed ? `\n${failed} check(s) failed` : '\nThe whole journey worked in the browser');
process.exit(failed ? 1 : 0);
