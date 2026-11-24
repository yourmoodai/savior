import assert from 'node:assert/strict';
import test from 'node:test';
import {chmodSync,mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {eligible,git,prose_edit} from './policy.mjs';

const before = 'Savior preserves the original text.\n', after = 'Savior preserves the\noriginal text.\n';
test('only mechanical edits to existing top-level prose paragraphs qualify', () => {
  assert.ok(prose_edit(before,after));
  assert.ok(prose_edit('The replacment.\n','The replacement.\n'));
  for (const [left,right] of [[before,'\n'+after],[before,after+'\n'],[before,before+after],
    [before,'Savior changes the original text.\n'],[before,'Savior preserves [text](https://example.org).\n'],
    [before,'Savior preserves the <em>original</em> text.\n'],['The command runs locally.\n','The command runs\nlocally.\n'],
    ['~~~sh\n'+before+'~~~\n','~~~sh\n'+after+'~~~\n'],['    '+before,'    '+after],
    ['<!-- generated -->\n'+before,'<!-- generated -->\n'+after],['---\n'+before+'---\n','---\n'+after+'---\n']]) {
    assert.equal(prose_edit(left,right),false);
  }
});

test('complete trees bind file count, modes, path and base ancestry', t => {
  const dir = mkdtempSync(join(tmpdir(),'savior-policy-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  git(dir,['init','-q','--template=']);
  const commit = () => {
    git(dir,['add','-A']);
    git(dir,['-c','user.name=Test','-c','user.email=test@example.org','-c','commit.gpgsign=false',
      '-c','core.hooksPath=/dev/null','commit','-qm','fixture']);
    return git(dir,['rev-parse','HEAD']).toString().trim();
  };
  const doc = join(dir,'README.md');
  writeFileSync(doc,before);
  const base = commit();
  writeFileSync(doc,after);
  const prose = commit();
  assert.ok(eligible(dir,base,prose,1));
  assert.equal(eligible(dir,prose,base,1),false);
  assert.equal(eligible(dir,base,prose,2),false);
  chmodSync(doc,0o755);
  assert.equal(eligible(dir,base,commit(),1),false);
  chmodSync(doc,0o644);
  writeFileSync(join(dir,'sign.py'),'pass\n');
  const mixed=commit();
  assert.equal(eligible(dir,base,mixed,2),false);
  git(dir,['mv','README.md','other.md']);
  assert.equal(eligible(dir,mixed,commit(),2),false);
  git(dir,['mv','other.md','AGENTS.md']);
  const policy=commit();
  writeFileSync(join(dir,'AGENTS.md'),before);
  assert.equal(eligible(dir,policy,commit(),1),false);
});
