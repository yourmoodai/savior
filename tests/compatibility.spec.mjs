import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ClientCompatibility, select_adapter} from '../hooks/compatibility.ts';
import {catalog} from '../hooks/compatibility-catalog.ts';

test('adapter selection is exact and follows the qualified native client pin', () => {
  const version=JSON.parse(readFileSync(new URL('../tools/host/package.json',import.meta.url))).dependencies['@anthropic-ai/claude-code'];
  assert.equal(select_adapter(version)?.id,'composer_2_1_322');
  for (const candidate of ['2.1.321','2.1.323','3.0.0','2.1.322-dev','2.1.322 (Claude Code)',undefined]) {
    assert.equal(select_adapter(candidate),null);
  }
});

test('separate API entries select independently and ambiguous mappings refuse', () => {
  const current = catalog.adapters[0];
  const future = {...current,id:'synthetic_next_api',client_versions:['3.0.0'],entry:'hooks/synthetic-next.ts'};
  assert.equal(select_adapter('3.0.0',[current,future]).id,'synthetic_next_api');
  assert.equal(select_adapter('2.1.322',[current,future]).id,current.id);
  assert.equal(select_adapter('2.1.322',[current,current]),null);
});

test('client probe failure refuses and a session retains one API contract', async () => {
  const client=new ClientCompatibility();
  assert.equal(await client.check(async()=>{throw Error('missing API')}),false);
  assert.match(client.status,/unsupported/);
  assert.equal(await client.check(async()=>({version:'2.1.322'})),false);
  client.reset();
  assert.equal(await client.check(async()=>({version:'2.1.322'})),true);
  assert.equal(await client.check(async()=>{throw Error('must not reprobe')}),true);
});
