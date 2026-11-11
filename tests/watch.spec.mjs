import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, mkdir, writeFile, readFile, readdir, rm, realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';

const binary = resolve('target/release/savior' + (process.platform === 'win32' ? '.exe' : ''));
const review = resolve('target/release/savior-review' + (process.platform === 'win32' ? '.exe' : ''));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function start(command, args, env, input) {
  const child = spawn(command, args, {env, stdio:['pipe','pipe','pipe']});
  const result = {child, stdout:'', stderr:'', closed:false};
  child.stdout.on('data', chunk => result.stdout += chunk);
  child.stderr.on('data', chunk => result.stderr += chunk);
  result.done = new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('close', (code, signal) => { result.closed = true; resolve({code, signal}); });
  });
  child.stdin.end(input);
  return result;
}

async function wait_for(predicate, process) {
  const deadline = Date.now() + 10000;
  while (!predicate()) {
    assert.ok(!process.closed, process.stderr || 'watcher exited');
    assert.ok(Date.now() < deadline, process.stderr || 'watch timeout');
    await sleep(10);
  }
}

async function finish(process) {
  if (!process.closed) process.child.kill('SIGINT');
  const timer = setTimeout(() => process.child.kill('SIGKILL'), 3000);
  try { await process.done; } finally { clearTimeout(timer); }
}

async function fixture(run) {
  const directory = await realpath(await mkdtemp(join(tmpdir(), 'savior-watch-')));
  const profile = join(directory, 'profile');
  const model = join(directory, 'model');
  await mkdir(profile); await mkdir(model);
  await writeFile(join(model,'decision.json'), JSON.stringify({
    version:1, kind:'lexical', policy:'offensive_spans', features:'unicode_words_separated_ascii_v2',
    threshold:0.5, corpus_sha256:'a'.repeat(64), epoch:0, labels_reviewed:false,
    weights:{idiot:1, stupid:1, jerk:1}, transitions:[[0,0],[0,0]],
  }));
  const env = {...process.env, CLAUDE_CONFIG_DIR:profile, XDG_STATE_HOME:join(directory,'state'),
    LOCALAPPDATA:join(directory,'local'), SAVIOR_MODEL_DIR:model, SAVIOR_DETECTOR:'lexical',
    SAVIOR_POLICY:'offensive_spans', SAVIOR_THRESHOLD:'0.5', SAVIOR_REPLACEMENT_STRATEGY:'fixed_phrase'};
  delete env.SAVIOR_RUNTIME_DIR;
  delete env.SAVIOR_REPLACEMENT;
  try { await run(env, directory); }
  finally {
    await rm(directory, {recursive:true, force:true, maxRetries:20, retryDelay:100});
    if (process.platform !== 'win32') {
      const hash = createHash('sha256').update(profile).digest('hex');
      await rm(`/tmp/savior-${process.geteuid()}-${hash}`, {recursive:true, force:true});
    }
  }
}

async function rewrite(env, text, version = 3) {
  const process = start(globalThis.process.execPath, ['bridge/cli.mjs','rewrite'], env, JSON.stringify({version,text}));
  assert.equal((await process.done).code, 0, process.stderr);
  return JSON.parse(process.stdout);
}

async function assert_no_saved_text(directory) {
  for (const entry of await readdir(directory, {withFileTypes:true})) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) await assert_no_saved_text(path);
    else {
      // Include SQLite WAL files and projections, not just the main database.
      const bytes = await readFile(path);
      assert.ok(!bytes.includes(Buffer.from('keep café')));
      assert.ok(!bytes.includes(Buffer.from('idiot stupid')));
    }
  }
}

for (const view of ['spans','span-diff','prompt-diff']) {
  test(`live ${view} displays exact plugin rewrites with trace capture disabled`, {timeout:30000}, async () => fixture(async env => {
    const watcher = start(binary, ['watch','--view',view], env);
    try {
      await wait_for(() => watcher.stderr.includes('watching live'), watcher);
      const result = await rewrite(env, '🌱 idiot stupid!\nkeep café, jerk.');
      assert.equal(result.text, '🌱 ya ya ya!\nkeep café, ya ya ya.');
      await wait_for(() => watcher.stdout.includes('jerk'), watcher);
      assert.ok(watcher.stdout.includes('idiot stupid'));
      assert.equal(watcher.stdout.includes('keep café'), view === 'prompt-diff');
      assert.equal(watcher.stdout.includes('ya ya ya'), view !== 'spans');
      if (view === 'span-diff') assert.ok(watcher.stdout.includes('- idiot stupid\n+ ya ya ya'));
      if (view === 'prompt-diff') assert.ok(watcher.stdout.includes('🌱 [-idiot stupid-]{+ya ya ya+}!\nkeep café, [-jerk-]{+ya ya ya+}.'));
      assert.ok(!watcher.stdout.includes('\x1b'));

      const status = start(review, ['traces','status'], env);
      assert.equal((await status.done).code, 0, status.stderr);
      assert.equal(JSON.parse(status.stdout).full_text, false);
      const state = process.platform === 'win32' ? join(env.LOCALAPPDATA,'savior','state') : join(env.XDG_STATE_HOME,'savior');
      const profiles = await readdir(join(state,'profiles'));
      assert.equal(profiles.length, 1);
      await assert_no_saved_text(state);
    } finally { await finish(watcher); }
  }));
}

test('watch is isolated, exclusive, live only, and ignores unchanged or failed rewrites', {timeout:30000}, async () => fixture(async (env, directory) => {
  await rewrite(env, 'idiot before watching');
  const watcher = process.platform === 'win32' ? start(binary, ['watch'], env)
    : start(process.execPath, ['bridge/cli.mjs','watch'], env);
  try {
    await wait_for(() => watcher.stderr.includes('watching live'), watcher);
    const duplicate = start(binary, ['watch'], env);
    assert.equal((await duplicate.done).code, 1);
    assert.ok(duplicate.stderr.includes('watch_already_running'), duplicate.stderr);
    await rewrite(env, 'keep this unchanged');
    assert.equal((await rewrite(env, '`idiot`')).status, 'error');
    await rewrite(env, 'idiot legacy caller', 1);
    const other = join(directory,'other'); await mkdir(other);
    await rewrite({...env, CLAUDE_CONFIG_DIR:other}, 'idiot other profile');
    assert.equal(watcher.stdout, '');
    await rewrite(env, 'idiot current profile');
    await wait_for(() => watcher.stdout.includes('idiot'), watcher);
    assert.equal(watcher.stdout.match(/processed/g).length, 1);
  } finally { await finish(watcher); }
  const restarted = start(binary, ['watch'], env);
  try {
    await wait_for(() => restarted.stderr.includes('watching live'), restarted);
    assert.equal(restarted.stdout, '');
    await rewrite(env, 'jerk after restart');
    await wait_for(() => restarted.stdout.includes('jerk'), restarted);
  } finally { await finish(restarted); }
}));

test('closed output does not block rewriting and watcher exits', {timeout:30000}, async () => fixture(async env => {
  const watcher = start(binary, ['watch'], env);
  try {
    await wait_for(() => watcher.stderr.includes('watching live'), watcher);
    watcher.child.stdout.destroy();
    assert.equal((await rewrite(env, 'idiot')).text, 'ya ya ya');
    await wait_for(() => watcher.closed, watcher);
  } finally { await finish(watcher); }
}));

test('watch validates options without loading a model', {timeout:10000}, async () => {
  for (const args of [['watch','--view','unknown'], ['watch','--extra']]) {
    const result = start(binary, args, {...process.env,SAVIOR_MODEL_DIR:'/missing'});
    assert.equal((await result.done).code, 1);
    assert.equal(result.stdout, '');
    assert.ok(!result.stderr.includes('model'));
  }
});

test('slow output drops display events without blocking rewrites', {timeout:30000}, async () => fixture(async env => {
  const watcher = start(binary, ['watch','--view','prompt-diff'], env);
  try {
    await wait_for(() => watcher.stderr.includes('watching live'), watcher);
    watcher.child.stdout.pause();
    const script = `
      const {loadDetector} = require('./native/index.cjs');
      (async () => {
        const detector = await loadDetector({modelDir:process.env.SAVIOR_MODEL_DIR, detector:'lexical'});
        // Fill the output pipe without making span serialization the bottleneck.
        const text = 'idiot! ' + 'keep '.repeat(12000);
        for (let i = 0; i < 40; i++) {
          const result = JSON.parse(await detector.rewriteTraced(text));
          if (result.status !== 'rewritten' || result.replacement_count !== 1 ||
              result.text !== 'ya ya ya! ' + 'keep '.repeat(12000)) throw Error('rewrite_failed');
        }
        console.log('completed');
      })().catch(() => process.exit(1));
    `;
    const publisher = start(process.execPath, ['-e',script], env);
    assert.equal((await publisher.done).code, 0, publisher.stderr);
    assert.equal(publisher.stdout.trim(), 'completed');
    await wait_for(() => watcher.stderr.includes('watch_event_dropped'), watcher);
  } finally { await finish(watcher); }
}));
