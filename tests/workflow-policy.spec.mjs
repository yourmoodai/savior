import {test} from 'node:test';
import assert from 'node:assert/strict';
import policy from '../scripts/release/workflow-policy.cjs';

const dependabot = {updates:[{'package-ecosystem':'github-actions',directories:['/','/.github/actions/example']}]};
const pinned = 'actions/example@'+'a'.repeat(40);
function documents(uses=pinned) {
  return {'.github/workflows/ci.yml':{jobs:{check:{steps:[{uses:'./.github/actions/example'}]}}},
    '.github/actions/example/action.yml':{name:'example',description:'fixture',
      runs:{using:'composite',steps:[{uses}]}}};
}
test('pin policy includes dependencies inside composites and update coverage',()=>{
  policy.validate(documents(),dependabot,'1.99.1');
  assert.throws(()=>policy.validate(documents('actions/example@v1'),dependabot,'1.99.1'),/full commit sha/);
  assert.throws(()=>policy.validate(documents(),{updates:[{'package-ecosystem':'github-actions',directory:'/'}]},'1.99.1'),/composite update coverage/);
});
test('local action metadata and paths are validated',()=>{
  const missing = documents(); delete missing['.github/actions/example/action.yml'];
  assert.throws(()=>policy.validate(missing,dependabot,'1.99.1'),/local action missing/);
  const invalid = documents(); invalid['.github/actions/example/action.yml'].runs.steps = [{run:'echo fixture'}];
  assert.throws(()=>policy.validate(invalid,dependabot,'1.99.1'),/invalid composite step/);
});
test('checkout credentials and toolchain selection cannot drift',()=>{
  for (const step of [{uses:'actions/checkout@'+'b'.repeat(40)},
    {uses:'dtolnay/rust-toolchain@'+'c'.repeat(40),with:{toolchain:'stable'}}]) {
    const docs = documents(); docs['.github/actions/example/action.yml'].runs.steps=[step];
    assert.throws(()=>policy.validate(docs,dependabot,'1.99.1'));
  }
});
