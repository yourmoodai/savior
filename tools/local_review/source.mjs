import {execFileSync} from 'node:child_process';

export function git(args) {
  const data = execFileSync('git',['--no-replace-objects','--literal-pathspecs',...args],{maxBuffer:8_000_000});
  return new TextDecoder('utf-8',{fatal:true}).decode(data);
}

function tree(revision) {
  const entries = new Map();
  for (const row of git(['ls-tree','-rz',revision]).split('\0').filter(Boolean)) {
    const tab = row.indexOf('\t');
    const [mode,,oid] = row.slice(0,tab).split(' ');
    entries.set(row.slice(tab+1),{mode,oid});
  }
  return entries;
}

export function snapshot(pr) {
  if (![pr.base.sha,pr.head.sha].every(sha=>/^[a-f0-9]{40}$/.test(sha))) throw Error('invalid revision');
  const base = git(['merge-base',pr.base.sha,pr.head.sha]).trim();
  const range = [base,pr.head.sha];
  const diff = ['diff','--no-ext-diff','--no-textconv','--no-renames','--ignore-submodules=none'];
  const paths = git([...diff,'--name-only','-z',...range]).split('\0').filter(Boolean);
  if (!paths.length || paths.some(p=>/[\x00-\x1f\x7f]/.test(p))) throw Error('empty or unsupported review scope');
  const trees = {old:tree(base),new:tree(pr.head.sha)};
  const blobs = new Map();
  function read(path,side) {
    const entry = trees[side]?.get(path);
    if (!entry) return null;
    if (!['100644','100755'].includes(entry.mode)) throw Error('unsupported file mode: '+path);
    if (!blobs.has(entry.oid)) {
      const text = git(['cat-file','blob',entry.oid]);
      if (text.includes('\0')) throw Error('binary evidence: '+path);
      blobs.set(entry.oid,text);
    }
    return blobs.get(entry.oid);
  }
  for (const path of paths) {
    read(path,'old');
    read(path,'new');
  }
  if (/^-\t-\t/m.test(git([...diff,'--numstat',...range]))) throw Error('binary patch unavailable');
  return {paths,read,patch:git([...diff,...range]),available:[...new Set([...trees.new.keys(),...trees.old.keys()])]};
}
