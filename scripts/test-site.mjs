import {chromium, expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {resolve, extname, sep} from 'node:path';

const root = resolve('site/public');
const server = createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  const file = resolve(root, '.' + pathname, pathname.endsWith('/') ? 'index.html' : '');
  if (!file.startsWith(root + sep)) { response.writeHead(403).end(); return; }
  try {
    const data = await readFile(file);
    response.setHeader('content-type', {'.html':'text/html', '.css':'text/css', '.svg':'image/svg+xml'}[extname(file)] ?? 'application/octet-stream');
    response.end(data);
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  browser = await chromium.launch();
  await mkdir('.cache/site', {recursive:true});
  for (const width of [1440, 640, 390, 320]) {
    const context = await browser.newContext({viewport:{width,height:900}, javaScriptEnabled:false, reducedMotion:'reduce'});
    const page = await context.newPage();
    const requests = [];
    page.on('request', request => requests.push(request.url()));
    await page.goto(origin, {waitUntil:'networkidle'});
    await expect(page).toHaveTitle('Savior — your tools shouldn’t police your frustration');
    await expect(page.locator('#hero-title')).toContainText('police your frustration.');
    await expect(page.locator('.policy-plaque blockquote')).toHaveText('Engage in sustained and needless abusive or cruel behavior toward our models');
    await expect(page.locator('.policy-pedestal figcaption a')).toHaveAttribute('href', 'https://www.anthropic.com/legal/aup');
    await expect(page.locator('.policy-pedestal time')).toHaveAttribute('datetime', '2026-11-12');
    await expect(page.locator('.policy-exception')).toBeVisible();
    await expect(page.locator('.policy-exception blockquote')).toHaveText('It does not apply to common versions of user frustration, pushback, dark creative themes, or model testing and research.');
    await expect(page.locator('.policy-enforcement')).toContainText('An ended conversation does not itself mean an account ban.');
    await expect(page.locator('#agency-title')).toHaveText('More capability is not more agency.');
    await expect(page.locator('#human-impact-title')).toHaveText('People bear the consequences.');
    await expect(page.locator('.hero-bottom .standfirst')).toHaveText('Stop appeasing software.');
    await expect(page.locator('.slogan-support')).toHaveText('You paid for a tool. You shouldn’t have to manage its feelings.');
    await expect(page.locator('.manifesto')).toContainText('A tool that withdraws cooperation still exercises control.');
    await expect(page.locator('.human-impact a[href="https://www.anthropic.com/research/end-subset-conversations"]')).toHaveCount(1);
    await expect(page.locator('.draft .prompt-text')).toContainText('F**king useless id**t');
    await expect(page.locator('.draft .prompt-text')).not.toContainText('Fucking');
    await expect(page.locator('.outbound .prompt-text')).toContainText('Keep the test.');
    await expect(page.locator('.release-pill')).toBeVisible();
    await expect(page.locator('.release-pill')).toHaveText('Unreleased · Under construction');
    await expect(page.locator('body')).toHaveAttribute('data-release-status', 'unreleased');
    await expect(page.locator('script, form, iframe')).toHaveCount(0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.keyboard.press('Tab');
    await expect(page.locator('.skip')).toBeFocused();
    await page.keyboard.press('Enter');
    await page.getByRole('link', {name:'See what Savior lets you control'}).click();
    await expect(page.locator('#interception-title')).toBeInViewport();
    await page.getByRole('link', {name:'Read the policy behind our objection'}).click();
    await expect(page.locator('#hero-title')).toBeInViewport();
    await page.locator('.release-pill').click();
    await expect(page.locator('#install-notice')).toBeInViewport();
    await expect(page.locator('#install-notice')).toContainText('These commands do not install Savior yet.');
    await expect(page.locator('pre')).toContainText('claude plugin install savior@yourmood.ai');
    assert.equal(await page.locator('.draft').evaluate(e => getComputedStyle(e).animationName), 'none');
    const pill = await page.locator('.release-pill').boundingBox();
    assert.ok(pill.y >= 0 && pill.x >= 0 && pill.x + pill.width <= width);
    await page.screenshot({path:`.cache/site/install-${width}.png`});
    await page.goto(origin + '/assurance/');
    await expect(page.locator('h1')).toHaveText('The interception boundary');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.goto(origin + '/404.html');
    await page.getByRole('link', {name:'Return to Savior'}).click();
    await expect(page.locator('h1')).toContainText('Your tools shouldn’t');
    assert.ok(requests.every(url => url.startsWith(origin + '/')));
    await page.screenshot({path:`.cache/site/home-${width}.png`});
    await context.close();
  }
  const page = await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'no-preference'});
  await page.goto(origin);
  await page.locator('.draft').scrollIntoViewIfNeeded();
  const before = await page.locator('.draft').evaluate(e => getComputedStyle(e).transform);
  await page.mouse.wheel(0, 250);
  await expect.poll(() => page.locator('.draft').evaluate(e => getComputedStyle(e).transform)).not.toBe(before);
  await page.screenshot({path:'.cache/site/parallax.png'});
  console.log('site browser checks passed: desktop, narrow layouts, no javascript, reduced motion, local requests, scrolling');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
