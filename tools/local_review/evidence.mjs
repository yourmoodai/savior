import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import approval from '../adversarial_reviewer/approval.cjs';
import receipt from './receipt.cjs';

const {repository,run_base,cost_run,app_id,bot,check_name,marker}=receipt;
const prefix='repos/'+repository;
export function gh(args,options) {
  return execFileSync('gh',args,{timeout:120_000,maxBuffer:64_000_000,...options});
}
export function get(path) {
  return JSON.parse(gh(['api',prefix+path]));
}
export function list(path,key) {
  const pages=JSON.parse(gh(['api','--paginate','--slurp',prefix+path]));
  return pages.flatMap(page=>key ? page[key] : page);
}
const download=id=>gh(['api',prefix+'/actions/artifacts/'+id+'/zip']);
export function outstanding_findings(pr,reviews,fetch_check) {
  const own=reviews.filter(r=>r.user?.type==='Bot' && r.user.login===bot &&
    ['APPROVED','CHANGES_REQUESTED','DISMISSED'].includes(r.state)).sort((a,b)=>b.id-a.id)[0];
  if (own?.state!=='CHANGES_REQUESTED') return [];
  const id=/^<!-- savior-local-review:(\d+):/.exec(own.body)?.[1];
  if (!id) throw Error('unidentified outstanding local review');
  const check=fetch_check(id);
  const binding=receipt.receipt_binding(check.external_id);
  if (check.app?.id!==app_id || check.id!==Number(id) || check.name!==check_name || check.status!=='completed' || check.conclusion!=='failure' ||
      check.head_sha!==own.commit_id || binding.pr!==pr.number || binding.head!==own.commit_id ||
      !own.body.startsWith(marker(check))) throw Error('unverified outstanding local review');
  const text=/^```json\n([\s\S]+)\n```$/.exec(check.output.text)?.[1];
  const report=JSON.parse(text);
  if (report.verdict!=='changes_requested' || !Array.isArray(report.findings) || !report.findings.length)
    throw Error('missing outstanding local findings');
  return report.findings.map(f=>({path:f.path,details:f.reason,evidence:f.evidence,side:f.side,line:f.line}));
}

export function cost_evidence(pr) {
  const prefix='review-cost-block-'+pr.number+'-';
  const name=prefix+pr.head.sha+'-';
  const artifacts=list('/actions/artifacts?per_page=100','artifacts')
    .filter(a=>a.name.startsWith(prefix) && !a.expired).sort((a,b)=>b.id-a.id);
  function workflow(artifact) {return get('/actions/runs/'+artifact.workflow_run.id);}
  const records=[];
  for (const artifact of artifacts) {
    const match=/^([0-9a-f]{40})-([0-9a-f]{64})$/.exec(artifact.name.slice(prefix.length));
    if (!match || !artifact.workflow_run?.id) continue;
    const [,head,quote_id]=match;
    const run=workflow(artifact), target={...pr,head:{sha:head}};
    if (run_base(artifact,run,target)) records.push({artifact,head,quote_id,base:cost_run(artifact,run,target)});
  }
  const record=records.find(r=>r.artifact.name.startsWith(name) && r.base===pr.base.sha);
  if (!record) throw Error('no trusted budget stop for the current revision');
  const current=record.artifact;
  const quote=approval.verify_archive(download(current.id),current,pr,repository);
  function claims() {
    const rows=new Map();
    for (const {artifact,head,quote_id} of records) {
      const reports=list('/actions/runs/'+artifact.workflow_run.id+'/artifacts?per_page=100','artifacts')
        .filter(a=>a.name==='adversarial-report' && !a.expired);
      if (reports.length!==1 || reports[0].size_in_bytes>64_000_000) throw Error('missing or ambiguous previous assessment');
      const script=fileURLToPath(new URL('report.py',import.meta.url));
      const findings=JSON.parse(execFileSync('python3',[script,head,quote_id],
        {input:download(reports[0].id),maxBuffer:64_000_000,timeout:120_000}));
      for (const item of findings) {
        const path=item.file.replace(/:\d+$/,'');
        const claim={path,title:item.title,details:item.details,evidence:item.evidence,side:item.source_side};
        rows.set(JSON.stringify(claim),claim);
      }
    }
    const reviews=list('/pulls/'+pr.number+'/reviews?per_page=100');
    for (const claim of outstanding_findings(pr,reviews,id=>get('/check-runs/'+id)))
      rows.set(JSON.stringify(claim),claim);
    return [...rows.values()].map((row,i)=>({...row,id:i+1}));
  }
  function revalidate() {
    const artifact=get('/actions/artifacts/'+current.id);
    if (artifact.name!==current.name || cost_run(artifact,workflow(artifact),pr)!==pr.base.sha || Date.now()>=quote.expires_at*1000)
      throw Error('budget stop expired or changed');
  }
  return {artifact:current,quote,claims,revalidate};
}
