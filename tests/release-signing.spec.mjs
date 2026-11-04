import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash, generateKeyPairSync, sign} from 'node:crypto';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';

test('publication needs only the trusted public key and rejects tampering', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'savior-signature-'));
  t.after(() => rm(directory, {recursive:true, force:true}));
  const {privateKey, publicKey} = generateKeyPairSync('ed25519');
  const pem = publicKey.export({type:'spki',format:'pem'});
  const targets = {};
  for (const target of ['darwin-arm64','linux-x64','win32-x64']) {
    targets[target] = [];
    for (const name of ['savior','savior-review','savior.node']) {
      const filename = target === 'win32-x64' && name !== 'savior.node' ? name+'.exe' : name;
      const asset = target+'-'+filename;
      const bytes = Buffer.from('synthetic runtime '+asset);
      await writeFile(join(directory, asset), bytes);
      targets[target].push({name:filename,asset,bytes:bytes.length,
        sha256:createHash('sha256').update(bytes).digest('hex')});
    }
  }
  const version = JSON.parse(readFileSync(new URL('../package.json', import.meta.url))).version;
  const manifest = Buffer.from(JSON.stringify({schema:1,protocol:1,version,targets}));
  const signature = sign(null, manifest, privateKey);
  await writeFile(join(directory,'runtime-manifest.json'),manifest);
  await writeFile(join(directory,'runtime-manifest.sig'),signature);
  await writeFile(join(directory,'release-public.pem'),pem);
  function verify(key=pem) {
    const env = {...process.env};
    delete env.SAVIOR_RELEASE_SIGNING_KEY;
    delete env.SAVIOR_RELEASE_PUBLIC_KEY;
    if (key) env.SAVIOR_RELEASE_PUBLIC_KEY = key;
    return spawnSync(process.execPath,['scripts/release/verify-signature.mjs',directory],{env,encoding:'utf8'});
  }
  assert.equal(verify().status,0);
  assert.notEqual(verify(null).status,0);
  assert.notEqual(verify(generateKeyPairSync('ed25519').publicKey.export({type:'spki',format:'pem'})).status,0);
  await writeFile(join(directory,'runtime-manifest.sig'),Buffer.alloc(signature.length));
  assert.notEqual(verify().status,0);
  await writeFile(join(directory,'runtime-manifest.sig'),signature);
  await writeFile(join(directory,targets['linux-x64'][0].asset),Buffer.alloc(targets['linux-x64'][0].bytes));
  assert.notEqual(verify().status,0);
  await writeFile(join(directory,targets['linux-x64'][0].asset),Buffer.from('tampered runtime'));
  assert.notEqual(verify().status,0);
});
