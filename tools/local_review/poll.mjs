import {mkdirSync} from 'node:fs';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {app_client, pages, repository} from './github.mjs';
import {git, eligible} from './policy.mjs';
import {reconcile} from './coordinator.mjs';

function inspect(cache, pr) {
  // Read Git objects in an owned bare repository. No PR files or hooks execute.
  const remote = 'https://github.com/'+repository+'.git';
  git(cache,['-c','core.hooksPath=/dev/null','fetch','--no-tags','--no-recurse-submodules',
    '--no-write-fetch-head',remote,'refs/heads/main','refs/pull/'+pr.number+'/head']);
  return eligible(cache,pr.base.sha,pr.head.sha,pr.changed_files);
}

export async function poll({cache, canary}) {
  mkdirSync(cache,{recursive:true,mode:0o700});
  git(cache,['init','--quiet','--bare','--template=']);
  const api = await app_client(Boolean(canary));
  const open = await pages(api,'/pulls?state=open');
  let failed = false;
  for (const pr of open) {
    try {
      const duplicate = open.some(other => other.number !== pr.number && other.head.sha === pr.head.sha);
      const result = await reconcile(api,pr.number,current => !duplicate && inspect(cache,current),canary === pr.number);
      console.log(JSON.stringify(result));
    } catch {
      failed = true;
      console.error('review unavailable for pr '+pr.number);
    }
  }
  if (failed) throw Error('one or more prs require reconciliation');
  console.log(JSON.stringify({open:open.length,mode:canary ? 'canary' : 'observe'}));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const match = /^(?:--once)?(?: ?--canary ([1-9][0-9]*))?$/.exec(args.join(' '));
  if (!match) throw Error('usage: node poll.mjs [--once] [--canary PR]');
  const canary = Number(match[1]) || null;
  const cache = join(homedir(),'Library','Caches','ai.yourmood.savior.review','git');
  do {
    try { await poll({cache,canary}); }
    catch (error) {
      console.error(error.message);
      if (args.includes('--once')) process.exitCode = 1;
    }
    if (args.includes('--once')) break;
    await delay(60000);
  } while (true);
}
