import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import approval from '../adversarial_reviewer/approval.cjs';
import receipt from './receipt.cjs';

const {repository,cost_run}=receipt;
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
export function cost_evidence(pr) {
  const name='review-cost-block-'+pr.number+'-'+pr.head.sha+'-';
  const artifacts=list('/actions/artifacts?per_page=100','artifacts')
    .filter(a=>a.name.startsWith(name) && !a.expired).sort((a,b)=>b.id-a.id);
  if (!artifacts.length) throw Error('no budget-stop artifact for this head');
  const current=artifacts[0];
  function check(artifact) {
    return cost_run(artifact,get('/actions/runs/'+artifact.workflow_run.id),pr);
  }
  if (check(current)!==pr.base.sha) throw Error('stale budget-stop workflow');
  const quote=approval.verify_archive(download(current.id),current,pr,repository);
  function claims() {
    const rows=new Map();
    for (const artifact of artifacts) {
      check(artifact);
      const reports=list('/actions/runs/'+artifact.workflow_run.id+'/artifacts?per_page=100','artifacts')
        .filter(a=>a.name==='adversarial-report' && !a.expired);
      if (reports.length!==1 || reports[0].size_in_bytes>64_000_000) throw Error('missing or ambiguous previous assessment');
      const script=fileURLToPath(new URL('report.py',import.meta.url));
      const findings=JSON.parse(execFileSync('python3',[script,pr.head.sha,artifact.name.slice(name.length)],
        {input:download(reports[0].id),maxBuffer:4_000_000}));
      for (const item of findings) {
        const path=item.file.replace(/:\d+$/,'');
        const claim={path,title:item.title,details:item.details,evidence:item.evidence,side:item.source_side};
        rows.set(JSON.stringify(claim),claim);
      }
    }
    return [...rows.values()].map((row,i)=>({...row,id:i+1}));
  }
  function revalidate() {
    const artifact=get('/actions/artifacts/'+current.id);
    if (artifact.name!==current.name || check(artifact)!==pr.base.sha || Date.now()>=quote.expires_at*1000)
      throw Error('budget stop expired or changed');
  }
  return {artifact:current,quote,claims,revalidate};
}
