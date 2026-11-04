import { createHash, createPublicKey, verify } from 'node:crypto';
import { chmod, lstat, mkdir, open, readFile, readdir, rename, unlink } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { dirname, isAbsolute, join, parse, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('..', import.meta.url));
const targets = new Set(['darwin-arm64', 'linux-x64', 'win32-x64']);
const fail = code => { throw Error(code); };
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const bundle = 'runtime/darwin-arm64/SaviorRuntime.app';
const macos_runtime = bundle+'/Contents/Helpers';

function valid_digest(entry) {
  return entry && /^[a-f0-9]{64}$/.test(entry.sha256) &&
    Number.isSafeInteger(entry.bytes) && entry.bytes > 0 && entry.bytes <= 536_870_912; // 512 MiB
}

export function verify_manifest(bytes, signature, public_key, version, target) {
  if (!targets.has(target)) fail('unsupported_platform');
  const key = createPublicKey(public_key);
  if (key.asymmetricKeyType !== 'ed25519' || !verify(null, bytes, key, signature)) fail('invalid_release_signature');
  const manifest = JSON.parse(bytes);
  if (![1,2].includes(manifest.schema) || manifest.version !== version || manifest.protocol !== 1) fail('incompatible_release');
  const files = manifest.targets?.[target];
  const suffix = target === 'win32-x64' ? '.exe' : '';
  const expected = [`savior${suffix}`, `savior-review${suffix}`, 'savior.node'];
  if (!Array.isArray(files) || files.length !== expected.length) fail('invalid_release_manifest');
  for (const name of expected) {
    const entry = files.find(file => file.name === name);
    if (!valid_digest(entry)) fail('invalid_release_manifest');
    if (manifest.schema === 2 && target === 'darwin-arm64') {
      if (entry.path !== macos_runtime+'/'+name || entry.asset !== undefined) fail('invalid_release_manifest');
    } else if (!/^[a-zA-Z0-9._+-]+$/.test(entry.asset) || entry.path !== undefined) fail('invalid_release_manifest');
  }
  if (manifest.schema === 2) {
    const inventory = manifest.macos_bundle;
    if (!Array.isArray(inventory) || !inventory.length || inventory.length > 1000) fail('invalid_release_manifest');
    const seen = new Set();
    for (const entry of inventory) {
      if (!valid_digest(entry) || typeof entry.path !== 'string' ||
          !entry.path.startsWith(bundle+'/Contents/') ||
          !entry.path.split('/').every(part => /^[a-zA-Z0-9_.-]+$/.test(part) && !['.','..'].includes(part)) ||
          seen.has(entry.path)) fail('invalid_release_manifest');
      seen.add(entry.path);
    }
    for (const path of [bundle+'/Contents/Info.plist',bundle+'/Contents/MacOS/savior-bootstrap',
      bundle+'/Contents/Frameworks/node',...['savior','savior-review','savior.node'].map(name=>macos_runtime+'/'+name)]) {
      if (!seen.has(path)) fail('invalid_release_manifest');
    }
    for (const entry of manifest.targets['darwin-arm64'] || []) {
      const included=inventory.find(file=>file.path===entry.path);
      if (!included || included.bytes!==entry.bytes || included.sha256!==entry.sha256) fail('invalid_release_manifest');
    }
  }
  return manifest;
}

export async function verify_bundle(plugin_root, manifest) {
  const paths = [];
  async function visit(path) {
    const meta = await lstat(join(plugin_root,path));
    if (meta.isSymbolicLink()) fail('unsafe_cache_path');
    if (meta.isDirectory()) {
      for (const name of await readdir(join(plugin_root,path))) await visit(path+'/'+name);
    } else if (meta.isFile()) paths.push(path);
    else fail('unsafe_cache_path');
  }
  // Check ancestors as well as descendants: a redirected bundle is not trusted.
  for (const path of ['runtime','runtime/darwin-arm64']) {
    const meta=await lstat(join(plugin_root,path));
    if (!meta.isDirectory() || meta.isSymbolicLink()) fail('unsafe_cache_path');
  }
  await visit(bundle);
  const entries=manifest.macos_bundle;
  if(paths.length!==entries.length || paths.some(path=>!entries.some(entry=>entry.path===path))) fail('invalid_release_manifest');
  for (const entry of entries) if (!await matches(join(plugin_root,entry.path),entry)) fail('artifact_digest_mismatch');
}

export async function private_directory(path) {
  if (!isAbsolute(path)) fail('invalid_cache_path');
  if(path.split(sep).includes('..'))fail('unsafe_cache_path');
  let current=parse(path).root;
  for(const component of path.slice(current.length).split(sep).filter(Boolean)) {
    current=join(current,component);
    try {const meta=await lstat(current);if(meta.isSymbolicLink() || !meta.isDirectory())fail('unsafe_cache_path');}
    catch(e){if(e.code!=='ENOENT')throw e;}
  }
  const parent = dirname(path);
  if (parent !== path) {
    try { const m = await lstat(parent); if (m.isSymbolicLink() || !m.isDirectory()) fail('unsafe_cache_path'); }
    catch (e) { if (e.code !== 'ENOENT') throw e; await private_directory(parent); }
  }
  await mkdir(path, {mode:0o700, recursive:false}).catch(e => { if (e.code !== 'EEXIST') throw e; });
  const m = await lstat(path);
  if (m.isSymbolicLink() || !m.isDirectory()) fail('unsafe_cache_path');
  if (process.platform === 'win32') {
    const system=process.env.SystemRoot;
    if(!system || !isAbsolute(system))fail('private_cache_unavailable');
    const who = spawnSync(join(system,'System32','whoami.exe'), ['/user', '/fo', 'csv', '/nh'], {encoding:'utf8', windowsHide:true});
    const sid = who.stdout?.match(/S-\d+(?:-\d+)+/)?.[0];
    const acl=join(system,'System32','icacls.exe');
    if (who.status !== 0 || !sid || spawnSync(acl,[path,'/reset','/Q'],{windowsHide:true}).status !== 0 || spawnSync(acl, [path, '/inheritance:r', '/grant:r', `*${sid}:(OI)(CI)F`], {windowsHide:true}).status !== 0) fail('private_cache_unavailable');
  } else await chmod(path, 0o700);
}
async function regular(path) {
  try { const m = await lstat(path); if (!m.isFile() || m.isSymbolicLink() || m.nlink !== 1) fail('unsafe_cache_path'); return m; }
  catch (e) { if (e.code === 'ENOENT') return null; throw e; }
}
async function matches(path, entry) {
  const m = await regular(path);
  if (!m || m.size !== entry.bytes) return false;
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex') === entry.sha256;
}
export async function download_file(url, path, entry, fetcher = fetch) {
  if (await matches(path, entry)) return;
  const partial = path + '.partial';
  let offset = (await regular(partial))?.size ?? 0;
  if (offset === entry.bytes && await matches(partial, entry)) { await rename(partial, path); return; }
  if (offset >= entry.bytes) { await unlink(partial); offset = 0; }
  const response = await fetcher(url, {headers:offset ? {Range:`bytes=${offset}-`} : {}, signal:AbortSignal.timeout(480000)});
  if (response.status !== 200 && response.status !== 206) fail('download_failed');
  if (response.status === 206 && response.headers.get('content-range') !== `bytes ${offset}-${entry.bytes-1}/${entry.bytes}`) fail('invalid_download_range');
  if (response.status === 200) offset = 0;
  const file = await open(partial, offset ? 'a' : 'w', 0o600);
  try {
    let size = offset;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > entry.bytes) fail('download_too_large');
      await file.writeFile(chunk);
    }
    await file.sync();
  } finally { await file.close(); }
  if (!await matches(partial, entry)) fail('artifact_digest_mismatch');
  await rename(partial, path);
}

export async function prepare_runtime(plugin_root = root, fetcher = fetch) {
  const release = JSON.parse(await readFile(join(plugin_root, 'release.json')));
  const bytes = await readFile(join(plugin_root, 'runtime-manifest.json'));
  const signature = await readFile(join(plugin_root, 'runtime-manifest.sig'));
  const key = await readFile(join(plugin_root, 'release-public.pem'));
  const target = `${process.platform}-${process.arch}`;
  const manifest = verify_manifest(bytes, signature, key, release.version, target);
  if (manifest.schema === 2 && target === 'darwin-arm64') {
    await verify_bundle(plugin_root, manifest);
    return {status:'ready', runtime_dir:join(plugin_root,macos_runtime), manifest_sha256:sha(bytes)};
  }
  const cache = process.platform === 'win32' ? process.env.LOCALAPPDATA : (process.env.XDG_CACHE_HOME || join(process.env.HOME || '', '.cache'));
  if (!cache || !isAbsolute(cache)) fail('invalid_cache_path');
  const directory = join(cache, 'savior', 'releases', release.version, target, sha(bytes));
  await private_directory(directory);
  const lock_path = join(directory, 'setup.lock');
  await regular(lock_path);
  let lock;
  for (let attempt = 0; attempt < 600; attempt++) {
    try { lock = await open(lock_path, 'wx', 0o600); break; }
    catch (e) {
      if (e.code !== 'EEXIST') throw e;
      const pid = Number(await readFile(lock_path, 'utf8').catch(() => ''));
      if (Number.isSafeInteger(pid) && pid > 0) {
        try { process.kill(pid, 0); } catch (e) {
          if (e.code === 'ESRCH') {
            const recovery_path=join(directory,'recovery.lock');await regular(recovery_path);
            let recovery;
            try {recovery=await open(recovery_path,'wx',0o600);}catch(e){if(e.code!=='EEXIST')throw e;}
            if(recovery)try {
              const current=Number(await readFile(lock_path,'utf8').catch(()=>''));
              if(Number.isSafeInteger(current)&&current>0) {
                try {process.kill(current,0);}catch(e){if(e.code==='ESRCH')await unlink(lock_path);}
              }
            }finally{await recovery.close();await unlink(recovery_path);}
            await new Promise(resolve=>setTimeout(resolve,20));
            continue;
          }
        }
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  if (!lock) fail('setup_busy');
  try {
    await lock.writeFile(String(process.pid));
    for (const entry of manifest.targets[target]) {
      const path = join(directory, entry.name);
      await download_file(`https://github.com/yourmoodai/savior/releases/download/v${release.version}/${entry.asset}`, path, entry, fetcher);
      await chmod(path, entry.name.endsWith('.node') ? 0o600 : 0o700);
    }
  } finally { await lock.close(); await unlink(lock_path); }
  return {status:'ready', runtime_dir:directory, manifest_sha256:sha(bytes)};
}

if (import.meta.main) {
  prepare_runtime().then(value => process.stdout.write(JSON.stringify(value)+'\n')).catch(error => {
    const known=['unsupported_platform','invalid_release_signature','incompatible_release','invalid_release_manifest','invalid_cache_path','unsafe_cache_path','private_cache_unavailable','setup_busy','download_failed','invalid_download_range','download_too_large','artifact_digest_mismatch'];
    process.stdout.write(JSON.stringify({status:'error', code:known.includes(error.message)?error.message:'runtime_setup_failed'})+'\n'); process.exitCode = 1;
  });
}
