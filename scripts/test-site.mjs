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
    await expect(page).toHaveTitle('Savior — local prompt replacement for Claude Code');
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', await page.title());
    await expect(page.locator('meta[property="og:url"]')).toHaveAttribute('content', 'https://savior.yourmood.ai/');
    await expect(page.locator('meta[name="twitter:card"]')).toHaveAttribute('content', 'summary_large_image');
    const preview = await page.locator('meta[property="og:image"]').getAttribute('content');
    assert.equal(preview, 'https://savior.yourmood.ai/social-preview.png');
    const previewResponse = await context.request.get(origin + new URL(preview).pathname);
    assert.equal(previewResponse.status(), 200);
    const previewBytes = await previewResponse.body();
    assert.equal(previewBytes.subarray(1, 4).toString(), 'PNG');
    assert.equal(previewBytes.readUInt32BE(16), 1200);
    assert.equal(previewBytes.readUInt32BE(20), 630);
    await expect(page.locator('#hero-title')).toContainText('Depersonalize the prompt.');
    await expect(page.locator('.slogan-support')).toContainText('Text outside those phrases stays as you wrote it.');
    await expect(page.locator('#demo')).toContainText('It does not remove identifying information or anonymize you.');
    await expect(page.locator('.privacy')).toContainText('If processing fails, that submission stops.');
    await expect(page.locator('.manifesto')).toContainText('An ended conversation does not itself mean an account ban.');
    await expect(page.locator('.manifesto a[href="https://www.anthropic.com/legal/aup"]')).toHaveCount(1);
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
    await page.locator('.release-pill').click();
    await expect(page.locator('#install-title')).toBeInViewport();
    await page.locator('#install-notice').scrollIntoViewIfNeeded();
    await expect(page.locator('#install-notice')).toContainText('These commands do not install Savior yet.');
    await expect(page.locator('pre')).toContainText('claude plugin install savior@yourmood.ai');
    assert.equal(await page.locator('.draft').evaluate(e => getComputedStyle(e).animationName), 'none');
    const pill = await page.locator('.release-pill').boundingBox();
    assert.ok(pill.y >= 0 && pill.x >= 0 && pill.x + pill.width <= width);
    await page.screenshot({path:`.cache/site/install-${width}.png`});
    await page.goto(origin + '/assurance/');
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', 'https://savior.yourmood.ai/assurance/');
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', await page.title());
    await expect(page.locator('meta[name="description"]')).toHaveAttribute('content', /What Savior tests in Claude Code/);
    await expect(page.locator('h1')).toHaveText('The interception boundary');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.goto(origin + '/404.html');
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, follow');
    await expect(page.locator('link[rel="canonical"]')).toHaveCount(0);
    await page.getByRole('link', {name:'Return to Savior'}).click();
    await expect(page.locator('h1')).toContainText('Depersonalize the prompt.');
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
