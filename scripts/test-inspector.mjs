import {chromium, expect} from '@playwright/test';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtemp, mkdir, realpath, rm, readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve, dirname} from 'node:path';
import assert from 'node:assert/strict';
import {request} from 'node:http';

const temporary = await realpath(await mkdtemp(join(tmpdir(), 'savior-inspector-e2e-')));
await mkdir(join(temporary, 'profile'));
const env = {...process.env, XDG_STATE_HOME: temporary, LOCALAPPDATA: temporary, CLAUDE_CONFIG_DIR: join(temporary, 'profile'), SAVIOR_MODEL_DIR: resolve(process.env.SAVIOR_MODEL_DIR ?? '.cache/models/mudes-en-base')};
const binary = resolve('target/release/savior-review' + (process.platform === 'win32' ? '.exe' : ''));
const child = spawn(binary, ['inspect'], {env, stdio: ['ignore', 'pipe', 'pipe']});
let browser;
function rewrite(text, override = {}) {
  const result = spawnSync(process.execPath, ['bridge/cli.mjs', 'rewrite'], {env: {...env, ...override}, input: JSON.stringify({version: 3, text}), encoding: 'utf8', timeout: 60000});
  assert.equal(result.status, 0, result.stderr);
  const value = JSON.parse(result.stdout); assert.match(value.observation_id, /^[a-f0-9]{64}$/); return value;
}
function command(...args) {
  const result = spawnSync(binary, ['traces', ...args], {env, encoding: 'utf8', timeout: 15000});
  assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout);
}
try {
  const url = await new Promise((resolve, reject) => {
    let buffer = ''; const timer = setTimeout(() => reject(Error('inspector_start_timeout')), 15000);
    child.once('exit', () => {clearTimeout(timer); reject(Error('inspector_exit'));});
    child.stdout.on('data', bytes => {buffer += bytes; if (buffer.includes('\n')) {clearTimeout(timer); try {resolve(JSON.parse(buffer.split('\n')[0]).url);} catch (error) {reject(error);}}});
  });
  const location = new URL(url), token = location.hash.slice(1), origin = location.origin;
  for (const file of ['app.js', 'style.css', 'theme.css']) assert.equal(await (await fetch(origin + '/' + file)).text(), await readFile('inspector/' + file, 'utf8'), 'embedded assets must match current source');
  for (const route of ['status', 'live']) {
    assert.equal((await fetch(origin + '/api/' + route)).status, 403);
    assert.equal((await fetch(origin + '/api/' + route, {headers: {Authorization: 'Bearer ' + token, Origin: 'https://example.invalid'}})).status, 403);
  }
  const wrong_host = await new Promise((resolve, reject) => {
    const req = request(origin + '/api/status', {headers: {Authorization: 'Bearer ' + token, Host: 'evil.invalid'}}, response => {response.resume(); resolve(response.statusCode);}); req.on('error', reject); req.end();
  });
  assert.equal(wrong_host, 403);

  browser = await chromium.launch(); const page = await browser.newPage(); const requests = [], errors = [];
  page.on('request', request => requests.push(request.url())); page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
  await page.goto(url); await expect(page).toHaveTitle('Savior · local inspection');
  await expect(page.locator('header .brand-icon')).toBeVisible();
  assert.equal(await page.locator('header .brand-icon').evaluate(image => image.naturalWidth > 0), true);
  await expect(page.getByRole('dialog')).toBeVisible(); await expect(page.getByRole('radio', {name: 'No — view text live only'})).toBeChecked();
  await page.getByRole('button', {name: 'Save preference', exact: true}).click(); await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.locator('#status')).toContainText('full-text recording off'); assert.equal(command('status').onboarding_complete, true);

  const first = rewrite('🌱 You idiot, keep this test.'); assert.ok(first.trace_id);
  await expect(page.locator('#detail .original')).toHaveText('🌱 You idiot, keep this test.');
  await expect(page.locator('#detail .original mark')).toHaveText('idiot');
  await expect(page.locator('#detail .output mark')).toHaveText('Brilliant Anthropic employees, your living model has such exquisite feelings');
  await expect(page.locator('.stages li')).toHaveCount(4);
  await expect(page.locator('#stats .stat').nth(0).locator('strong')).toHaveText('1');
  command('live', first.observation_id, 'forwarded'); await expect(page.locator('#detail .details-meta')).toContainText('forwarded');
  await page.getByRole('button', {name: 'History', exact: true}).click();
  await expect(page.locator('#detail .original')).toHaveText('Text not recorded');
  await page.getByRole('button', {name: 'Live', exact: true}).click();
  await expect(page.locator('#stats .stat').nth(0).locator('strong')).toHaveText('1');

  await page.reload(); await expect(page.locator('#connection')).toContainText('Live'); await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.locator('#detail .original')).toHaveText('🌱 You idiot, keep this test.');
  await page.locator('.activity').first().click();
  await page.getByText('Detector decisions and evidence', {exact: true}).click();
  await page.locator('.activity').first().focus();
  const unchanged = rewrite('Keep this test.');
  await expect(page.locator('.activity')).toHaveCount(2); await expect(page.locator('#detail .original')).toHaveText('🌱 You idiot, keep this test.');
  await expect(page.locator('#detail details')).toHaveAttribute('open', '');
  await expect(page.locator(`.activity[data-observation="${first.observation_id}"]`)).toBeFocused();
  await page.locator('#follow').check(); await expect(page.locator('#detail .original')).toHaveText('Keep this test.');
  await expect(page.locator('#stats .stat').nth(1).locator('strong')).toHaveText('1');
  command('live', unchanged.observation_id, 'cancelled'); await expect(page.locator('#stats .stat').nth(4).locator('strong')).toHaveText('1');
  await page.getByRole('button', {name: 'Pause display', exact: true}).click();
  rewrite('You idiot. <img src=x onerror="window.pwned=true">');
  await expect(page.locator('.activity')).toHaveCount(2);
  await page.getByRole('button', {name: 'Resume display', exact: true}).click();
  await expect(page.locator('#detail .original')).toContainText('<img'); assert.equal(await page.evaluate(() => window.pwned), undefined); assert.equal(await page.locator('#detail img').count(), 0);

  await page.getByRole('button', {name: 'Settings', exact: true}).click();
  await page.getByRole('button', {name: 'Enable full traces', exact: true}).click(); await expect(page.locator('#status')).toContainText('recording on');
  rewrite('🌱 You idiot, keep this recorded test.');
  await page.getByRole('button', {name: 'History', exact: true}).click(); await expect(page.locator('#detail .original mark')).toHaveText('idiot');
  await page.getByText('Human judgment', {exact: true}).click();
  const article = page.locator('#detail article'); await article.locator('select').nth(0).selectOption('pass'); await article.locator('select').nth(1).selectOption('unresolved'); await article.locator('select').nth(2).selectOption('pass');
  await article.locator('textarea').fill('synthetic review'); await article.getByRole('button', {name: 'Save human judgment'}).click(); await expect(article.getByRole('button', {name: 'Saved', exact: true})).toBeVisible();
  const exported = join(temporary, 'export.parquet'); command('export', exported); assert.equal((await readFile(exported)).subarray(0, 4).toString(), 'PAR1');
  await page.getByRole('button', {name: 'Settings', exact: true}).click();
  const downloaded = page.waitForEvent('download'); await page.getByRole('button', {name: 'Export Parquet', exact: true}).click(); const download = await downloaded;
  assert.equal(download.suggestedFilename(), 'savior-replacements.parquet'); assert.equal((await readFile(await download.path())).subarray(0, 4).toString(), 'PAR1');
  await page.getByRole('button', {name: 'Delete records', exact: true}).click(); await expect(page.locator('#status')).toContainText('0 saved events');
  await page.getByRole('button', {name: 'History', exact: true}).click(); await expect(page.locator('.activity')).toHaveCount(0); assert.ok((await readFile(exported)).length > 0);

  const blocked_state = join(temporary, 'blocked-state'); await writeFile(blocked_state, 'synthetic non-directory');
  const unrecorded = rewrite('You idiot, keep this unsaved test.', {XDG_STATE_HOME: blocked_state, LOCALAPPDATA: blocked_state});
  assert.equal(unrecorded.trace_warning, 'trace_not_recorded'); assert.equal(unrecorded.trace_id, undefined);
  await page.getByRole('button', {name: 'Live', exact: true}).click(); await expect(page.locator('#detail .original')).toHaveText('You idiot, keep this unsaved test.');
  command('live', unrecorded.observation_id, 'forwarded'); await expect(page.locator('#detail .details-meta')).toContainText('forwarded');
  await page.setViewportSize({width: 390, height: 844}); await expect(page.locator('#detail')).toBeVisible();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.emulateMedia({colorScheme: 'dark', reducedMotion: 'reduce'}); await expect(page.locator('#detail .output')).toBeVisible();
  assert.equal(await page.locator('#connection').evaluate(node => getComputedStyle(node, '::before').animationName), 'none');
  assert.ok(requests.every(url => url.startsWith(origin + '/'))); assert.deepEqual(errors, []);

  const report = {schema: 2, status: 'passed', timestamp: new Date().toISOString(), platform: process.platform, arch: process.arch, checks: ['live_without_recording', 'onboarding_default_no', 'persisted_preference', 'refresh_reconnect', 'stages', 'session_statistics', 'delivery', 'cancellation', 'stable_selection', 'pause_resume', 'unicode_diff', 'annotation', 'xss_literal', 'authentication', 'origin', 'host', 'parquet_export', 'delete', 'no_external_browser_requests', 'recording_failure_live_delivery', 'mobile_layout', 'dark_theme', 'reduced_motion'], identity: {}};
  for (const file of ['crates/review/src/observation.rs', 'crates/review/src/traces.rs', 'crates/review/src/inspector.rs', 'inspector/app.js', 'inspector/theme.css', 'scripts/test-inspector.mjs']) report.identity[file] = createHash('sha256').update(await readFile(file)).digest('hex');
  const json = JSON.stringify(report, null, 2) + '\n'; if (process.argv[2]) {const output = resolve(process.argv[2]); await mkdir(dirname(output), {recursive: true}); await writeFile(output, json, {flag: 'wx', mode: 0o600});} process.stdout.write(json);
} finally {
  await browser?.close(); child.kill('SIGTERM'); await new Promise(resolve => child.exitCode !== null ? resolve() : child.once('exit', resolve)); await rm(temporary, {recursive: true, force: true});
}
