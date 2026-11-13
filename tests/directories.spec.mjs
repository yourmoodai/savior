import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,chmod,mkdir,symlink,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {cache_root,socket_directory} from '../bridge/directories.mjs';

test('cache paths honor absolute XDG overrides and ignore empty or relative values', {skip:process.platform==='win32'},()=>{
  for(const value of [undefined,'','relative']) assert.equal(cache_root({HOME:'/home/test',XDG_CACHE_HOME:value}),'/home/test/.cache');
  assert.equal(cache_root({XDG_CACHE_HOME:'/private/cache space/é'}),'/private/cache space/é');
  for(const env of [{},{HOME:''},{HOME:'relative'}]) assert.throws(()=>cache_root(env),/invalid_cache_path/);
});

test('runtime directories require ownership and privacy and retain bounded sockets', {skip:process.platform==='win32'},async()=>{
  const root=await mkdtemp('/tmp/sv');
  try {
    await chmod(root,0o700);
    assert.ok((await socket_directory('/synthetic/profile',{XDG_RUNTIME_DIR:root})).startsWith(root+'/'));
    await chmod(root,0o755);
    await assert.rejects(socket_directory('/synthetic/profile',{XDG_RUNTIME_DIR:root}),/invalid_runtime_path/);
    await chmod(root,0o700);
    const alias=join(root,'alias');await symlink(root,alias);
    await assert.rejects(socket_directory('/synthetic/profile',{XDG_RUNTIME_DIR:alias}),/invalid_runtime_path/);
    const long=join(root,'x'.repeat(120));await mkdir(long,{mode:0o700});
    const fallback=await socket_directory('/synthetic/profile',{XDG_RUNTIME_DIR:long});
    assert.ok(fallback.startsWith('/tmp/savior-'));
    assert.ok(Buffer.byteLength(fallback)+6<=100);
    assert.equal(await socket_directory('/synthetic/profile',{XDG_RUNTIME_DIR:root+'/./'.repeat(40)}),fallback);
  } finally {await rm(root,{recursive:true,force:true});}
});
