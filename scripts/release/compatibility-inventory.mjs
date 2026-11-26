import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {dirname, resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {catalog} from '../../hooks/compatibility-catalog.js';

export const targets = {'darwin-arm64':'macos-15', 'linux-x64':'ubuntu-24.04', 'linux-arm64':'ubuntu-24.04-arm', 'win32-x64':'windows-2022'};
export const stable_version = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const day = 86400000;
const source = 'https://api.github.com/repos/anthropics/claude-code/releases';

export function window_releases(releases, as_of) {
  const end = Date.parse(as_of), start = end - 90 * day;
  if (!Number.isFinite(end)) throw Error('invalid_inventory_date');
  const seen = new Set();
  return releases.filter(release => !release.draft && !release.prerelease &&
    /^v\d+\.\d+\.\d+$/.test(release.tag_name) &&
    Date.parse(release.published_at) >= start && Date.parse(release.published_at) <= end)
    .map(release => {
      const version = release.tag_name.slice(1);
      if (seen.has(version) || !stable_version.test(version) || !Number.isSafeInteger(release.id)) throw Error('invalid_release_identity');
      seen.add(version);
      return {version, published_at:release.published_at, release_id:release.id,
        release_url:`https://github.com/anthropics/claude-code/releases/tag/v${version}`};
    }).sort((a,b) => Date.parse(a.published_at) - Date.parse(b.published_at) || a.version.localeCompare(b.version));
}

function inventory_targets(inventory) {
  if (inventory.schema === 2) return targets;
  const {'linux-arm64': arm64, ...original} = targets;
  return original;
}

export function validate_inventory(inventory) {
  if (![1,2].includes(inventory.schema) || inventory.source !== source || inventory.window_days !== 90 ||
      !Number.isFinite(Date.parse(inventory.as_of)) ||
      Date.parse(inventory.window_start) !== Date.parse(inventory.as_of) - 90 * day ||
      !Array.isArray(inventory.releases) || !inventory.releases.length) throw Error('invalid_inventory');
  const seen = new Set();
  for (const release of inventory.releases) {
    if (!stable_version.test(release.version) || seen.has(release.version) || !Number.isSafeInteger(release.release_id) ||
        release.release_url !== `https://github.com/anthropics/claude-code/releases/tag/v${release.version}` ||
        !(Date.parse(release.published_at) >= Date.parse(inventory.window_start) && Date.parse(release.published_at) <= Date.parse(inventory.as_of)) ||
        Object.keys(release.artifacts ?? {}).sort().join() !== Object.keys(inventory_targets(inventory)).sort().join()) throw Error('invalid_release_identity');
    seen.add(release.version);
    for (const [target, artifact] of Object.entries(release.artifacts)) {
      const name = `@anthropic-ai/claude-code-${target}`;
      if (artifact === null) continue;
      if (artifact.name !== name || artifact.version !== release.version ||
          artifact.url !== `https://registry.npmjs.org/${name}/-/claude-code-${target}-${release.version}.tgz` ||
          !/^sha512-[A-Za-z0-9+/]{86}==$/.test(artifact.integrity)) throw Error('invalid_artifact_identity');
    }
  }
  return inventory;
}

async function json(url, headers = {}) {
  const response = await fetch(url, {headers, signal:AbortSignal.timeout(60000)});
  if (response.status === 404 && url.startsWith('https://registry.npmjs.org/')) return null;
  if (!response.ok) throw Error(`discovery_http_${response.status}`);
  return response.json();
}

export async function discover(as_of = new Date().toISOString()) {
  const releases = [];
  // Read every page: GitHub's ordering is not a publication-date guarantee.
  for (let page = 1; ; page++) {
    const batch = await json(`${source}?per_page=100&page=${page}`, {
      'User-Agent':'savior-compatibility', 'Accept':'application/vnd.github+json',
      ...(process.env.GH_TOKEN ? {Authorization:`Bearer ${process.env.GH_TOKEN}`} : {}),
    });
    if (!Array.isArray(batch)) throw Error('invalid_release_response');
    releases.push(...batch);
    if (batch.length < 100) break;
  }
  const selected = window_releases(releases, as_of);
  const packages = Object.fromEntries(await Promise.all(Object.keys(targets).map(async target =>
    [target, await json(`https://registry.npmjs.org/@anthropic-ai%2fclaude-code-${target}`, {'Accept':'application/vnd.npm.install-v1+json'})])));
  for (const release of selected) release.artifacts = Object.fromEntries(Object.keys(targets).map(target => {
    const pkg = packages[target]?.versions?.[release.version];
    return [target, pkg ? {name:pkg.name, version:pkg.version, url:pkg.dist?.tarball, integrity:pkg.dist?.integrity} : null];
  }));
  return validate_inventory({schema:2, source, as_of, window_days:90,
    window_start:new Date(Date.parse(as_of) - 90 * day).toISOString(), releases:selected});
}

export function job_matrix(inventory, adapters = catalog.adapters) {
  validate_inventory(inventory);
  const include = [];
  const reviewed = new Set(adapters.flatMap(adapter=>adapter.client_versions));
  // Each reviewed client also runs the installed harness, which dominates probe time.
  const batches = [];
  let batch = [], weight = 0;
  for (const {version} of inventory.releases) {
    const cost = reviewed.has(version) ? 6 : 1;
    if (weight + cost > 12) {
      batches.push(batch);
      batch = [];
      weight = 0;
    }
    batch.push(version);
    weight += cost;
  }
  batches.push(batch);
  for (const [target, os] of Object.entries(inventory_targets(inventory))) {
    for (const [index, versions] of batches.entries()) {
      include.push({target, os, batch:index, versions:versions.join(',')});
    }
  }
  if (include.length > 256) throw Error('inventory_exceeds_job_limit');
  return {include};
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [command, file, as_of] = process.argv.slice(2);
  if (command === 'discover' && file) {
    const inventory = await discover(as_of);
    await mkdir(dirname(resolve(file)), {recursive:true});
    await writeFile(file, JSON.stringify(inventory,null,2)+'\n', {flag:'wx'});
    console.log(JSON.stringify({releases:inventory.releases.length, combinations:inventory.releases.length * Object.keys(targets).length, as_of:inventory.as_of}));
  } else if (command === 'matrix' && file) {
    console.log(JSON.stringify(job_matrix(JSON.parse(await readFile(file)))));
  } else throw Error('usage: compatibility-inventory.mjs discover|matrix file [as_of]');
}
