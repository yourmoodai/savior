import {pathToFileURL} from 'node:url';
import {agent} from './agent.mjs';
import {assess} from './assess.mjs';
import {snapshot,git} from './source.mjs';
import {get,gh,list,cost_evidence} from './evidence.mjs';
import {app_client} from './github.mjs';
import receipt from './receipt.cjs';

const {repository,repo_id,app_id,bot,check_name,receipt_id,marker}=receipt;
function current(pr,original) {
  if (pr.state!=='open' || pr.draft || pr.base.ref!=='main' || pr.base.repo.id!==repo_id ||
      pr.head.repo?.id!==repo_id || pr.base.sha!==original.base.sha || pr.head.sha!==original.head.sha)
    throw Error('PR is stale or ineligible');
}

export async function complete({pr,agent,evidence,existing,inspect,write,refresh,reviews,checks}) {
  const id=receipt_id(pr,evidence.artifact.id,agent.identity);
  let check=existing.find(c=>c.external_id===id && c.app?.id===app_id && c.name===check_name &&
    c.head_sha===pr.head.sha && c.status==='completed' && ['success','failure'].includes(c.conclusion));
  let writer;
  if (!check) {
    const result=await inspect();
    const report=JSON.stringify({agent:agent.name,...result},null,2).replace(/[`<>&@]/g,
      c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
    const text='```json\n'+report+'\n```';
    if (Buffer.byteLength(text)>65_535) throw Error('report exceeds GitHub check output limit');
    const clean=result.verdict==='approve';
    await refresh();
    writer=await write();
    check=await writer('/check-runs','POST',{name:check_name,head_sha:pr.head.sha,status:'completed',
      conclusion:clean?'success':'failure',external_id:id,output:{title:clean?'Approved':'Changes requested',
        summary:result.coverage.length+' files reviewed, '+result.findings.length+' findings, '+result.prior.length+' prior allegations checked.',text}});
  }
  const state=check.conclusion==='success'?'APPROVED':'CHANGES_REQUESTED';
  const stamp=marker(check);
  const latest=new Map();
  const competing=state==='APPROVED' ? await checks() : [];
  const history=await reviews();
  if (state==='APPROVED') receipt.assert_no_conflict(competing,history,pr);
  for (const row of history.sort((a,b)=>a.id-b.id)) {
    if (['APPROVED','CHANGES_REQUESTED','DISMISSED'].includes(row.state)) latest.set(row.user?.login || row.id,row);
  }
  const own=latest.get(bot);
  if (state==='APPROVED' && [...latest.values()].some(r=>r.state==='CHANGES_REQUESTED' &&
      (r.user?.login!==bot || r.commit_id===pr.head.sha))) throw Error('outstanding request for changes');
  const previous=/^<!-- savior-local-review:(\d+):/.exec(own?.body || '');
  if (previous && Number(previous[1])>check.id) throw Error('local review superseded');
  if (own?.body?.startsWith(stamp) && own.state==='DISMISSED') throw Error('local review dismissed');
  if (own?.state!==state || own.commit_id!==pr.head.sha || !own.body?.startsWith(stamp)) {
    await refresh();
    writer ||= await write();
    await writer('/pulls/'+pr.number+'/reviews','POST',{commit_id:pr.head.sha,
      event:state==='APPROVED'?'APPROVE':'REQUEST_CHANGES',body:stamp+'\n'+check.output.summary+' [Review report]('+check.html_url+').'});
  }
  return check;
}

async function main() {
  const [number,name,...extra]=process.argv.slice(2);
  if (!/^[1-9]\d*$/.test(number) || extra.length) throw Error('usage: npm run review:local -- <pr> [agy|claude|codex]');
  const reviewer=agent(name || process.env.SAVIOR_REVIEW_AGENT || 'agy');
  const path='/pulls/'+number, pr=get(path);
  current(pr,pr);
  git(['fetch','--no-tags','origin','refs/heads/main','refs/pull/'+number+'/head']);
  if (git(['rev-parse','HEAD']).trim()!==pr.base.sha ||
      git(['status','--porcelain','--untracked-files=all','--','tools/local_review','tools/adversarial_reviewer','package.json']).trim())
    throw Error('run from a clean checkout of trusted main');
  const evidence=cost_evidence(pr);
  const checks=()=>list('/commits/'+pr.head.sha+'/check-runs?filter=all&app_id='+app_id+'&per_page=100','check_runs');
  async function refresh() {
    current(get(path),pr);
    evidence.revalidate();
  }
  const check=await complete({pr,agent:reviewer,evidence,existing:checks(),checks,write:app_client.bind(null,true),refresh,
    inspect:()=>assess(snapshot(pr),evidence.claims(),reviewer.infer,console.info),reviews:()=>list(path+'/reviews?per_page=100')});
  console.log(check.output.summary,check.html_url);
  if (check.conclusion!=='success') {process.exitCode=1;return;}
  await refresh();
  const title='pr validation #'+pr.number+' local '+check.id;
  const runs=get('/actions/workflows/pr-validation.yml/runs?event=workflow_dispatch&branch=main&per_page=100').workflow_runs;
  const run=runs.find(r=>r.display_title===title && r.head_sha===pr.base.sha);
  if (run) {
    const status=get('/commits/'+pr.head.sha+'/status?per_page=100').statuses.find(s=>s.context==='pr readiness');
    const current_run=status?.target_url===run.html_url+'/attempts/'+run.run_attempt;
    if (run.status!=='completed' || (run.conclusion==='success' && current_run && status.state==='success')) {
      console.log(run.html_url);
      return;
    }
  }
  gh(['workflow','run','pr-validation.yml','--repo',repository,'--ref','main','-f','pr_number='+number,'-f','local_check_id='+check.id]);
  console.log('requested native validation for PR #'+number);
}
if (import.meta.url===pathToFileURL(process.argv[1] || '').href)
  main().catch(error=>{console.error(error.message);process.exitCode=1;});
