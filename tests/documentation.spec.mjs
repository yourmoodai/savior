import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cp, mkdtemp, readFile, readdir, rm} from 'node:fs/promises';
import {dirname, join, relative, resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';

const root = resolve(import.meta.dirname, '..');

async function documents(directory) {
  const found = [];
  for (const entry of await readdir(directory, {withFileTypes:true})) {
    const path = join(directory,entry.name);
    if (entry.isDirectory()) found.push(...await documents(path));
    else if (entry.name.endsWith('.md')) found.push(path);
  }
  return found;
}

function anchors(markdown) {
  const counts = new Map();
  return new Set([...markdown.matchAll(/^#{1,6}\s+(.+)$/gm)].map(([,title]) => {
    const slug = title.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu,'').replaceAll(' ','-');
    const count = counts.get(slug) ?? 0;
    counts.set(slug,count+1);
    return count ? `${slug}-${count}` : slug;
  }));
}

async function check_links(directory) {
  const paths = [join(directory,'README.md'), join(directory,'CONTRIBUTING.md'), ...await documents(join(directory,'docs'))];
  for (const path of paths) {
    const markdown = (await readFile(path,'utf8')).replace(/^```[^\n]*\n[\s\S]*?^```\s*$/gm,'');
    const targets = [...markdown.matchAll(/!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))\)/g)].map(match => match[1] ?? match[2]);
    targets.push(...[...markdown.matchAll(/<img\b[^>]*\bsrc="([^"]+)"/g)].map(match => match[1]));
    for (const target of targets) {
      if (/^[a-z][a-z\d+.-]*:/i.test(target)) continue;
      const [file, anchor] = target.split('#');
      const destination = file ? resolve(dirname(path),decodeURIComponent(file)) : path;
      const content = await readFile(destination,'utf8').catch(() => assert.fail(`${relative(directory,path)}: missing ${target}`));
      if (anchor && destination.endsWith('.md')) {
        assert.ok(anchors(content).has(decodeURIComponent(anchor)),`${relative(directory,path)}: missing anchor ${target}`);
      }
    }
  }
  return paths;
}

test('repository and packaged documentation resolve local links and anchors', async t => {
  const source_paths = await check_links(root);
  const destination = await mkdtemp(join(tmpdir(),'savior-docs-'));
  t.after(() => rm(destination,{recursive:true,force:true}));
  const script = `
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('release_package', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.copy_documentation(pathlib.Path(sys.argv[2]))
`;
  const copied = spawnSync(process.platform==='win32'?'python':'python3', ['-c',script,join(root,'scripts/release/package.py'),destination], {encoding:'utf8'});
  assert.equal(copied.status,0,copied.stderr);
  // The remaining link targets are already included by release assembly.
  for (const name of ['LICENSE','NOTICE','models']) await cp(join(root,name),join(destination,name),{recursive:true});
  await check_links(destination);
  for (const path of source_paths) {
    assert.deepEqual(await readFile(join(destination,relative(root,path))),await readFile(path));
  }
  const inventory = JSON.parse(await readFile(join(root,'package.json'))).files;
  for (const name of ['README.md','CONTRIBUTING.md','docs','benchmarks/results','benchmarks/sources.json','benchmarks/NOTICE.md']) {
    assert.ok(inventory.includes(name),`npm package omits ${name}`);
  }
});
