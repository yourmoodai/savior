import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {repository, repo_id} from './github.mjs';
import {git} from './policy.mjs';

const [number, status, filename] = process.argv.slice(2);
if (!/^[1-9][0-9]*$/.test(number) || !['instructions','reviewed','unavailable','declined','exhausted'].includes(status) ||
    (status === 'reviewed') !== Boolean(filename)) throw Error('usage: node author.mjs PR instructions|declined|unavailable|exhausted|reviewed [feedback-file]');
const gh = (...args) => execFileSync('gh',args,{encoding:'utf8',stdio:['pipe','pipe','inherit']}).trim();
const pr = JSON.parse(gh('api','repos/'+repository+'/pulls/'+number));
const me = JSON.parse(gh('api','user'));
if (pr.user.id !== me.id || pr.base.repo.id !== repo_id || pr.state !== 'open') throw Error('author binding unavailable');
const head = git(process.cwd(),['rev-parse','HEAD']).toString().trim();
if (pr.head.sha !== head || git(process.cwd(),['status','--porcelain']).length) throw Error('clean exact checkout required');
git(process.cwd(),['cat-file','-e',pr.base.sha+'^{commit}']);
if (status === 'instructions') {
  console.log('In your own authorized Copilot CLI session, confirm the account and billing selection, then enter:');
  console.log('/review Review the full diff from '+pr.base.sha+' to '+head);
  console.log('To share feedback, run this command again with reviewed and the path to your reviewed feedback.');
} else {
  // Explicit author invocation publishes advisory feedback against this revision only.
  const feedback = filename ? readFileSync(filename,'utf8').trim() : '';
  if (filename && (!feedback || feedback.length > 16000)) throw Error('invalid feedback length');
  const body = ['/savior author-review',pr.base.sha,head,status].join(' ')+(feedback ? '\n\n'+feedback : '');
  execFileSync('gh',['pr','comment',number,'--repo',repository,'--body-file','-'],{input:body,stdio:['pipe','inherit','inherit']});
}
