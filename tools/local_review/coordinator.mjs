import {bot, pages, repository, repo_id} from './github.mjs';
import {policy_version} from './policy.mjs';

export const check_name = 'Savior local review policy';
const app_id = 5910949;

function binding(pr) {
  return [pr.number, pr.base.repo.id, pr.base.ref, pr.base.sha, pr.head.repo?.id, pr.head.sha, pr.state, pr.draft].join('/');
}

export function author_route(pr, comments) {
  const prefix = ['/savior author-review', pr.base.sha, pr.head.sha, ''].join(' ');
  const receipt = comments.filter(c => c.user.id === pr.user.id && !c.performed_via_github_app &&
    c.body.startsWith(prefix)).at(-1)?.body.slice(prefix.length).split('\n')[0];
  if (receipt === 'reviewed') return 'author feedback available; independent review required';
  if (['declined','unavailable','exhausted'].includes(receipt)) return 'human review queued; bounded organization billing unavailable';
  return 'awaiting author opt-in or independent review';
}

export async function reconcile(api, number, inspect, canary = false) {
  const path = '/pulls/'+number;
  const pr = await api(path);
  const stamp = [repository+'#'+number, policy_version, pr.base.sha, pr.head.sha].join(' ');
  const current = async () => binding(await api(path)) === binding(pr);
  const writable = pr.state === 'open' && pr.base.repo.id === repo_id && pr.head.repo?.id === repo_id;
  let prose = false;
  if (writable && pr.base.ref === 'main') {
    try { prose = await inspect(pr); }
    catch { prose = false; }
  }
  const route = prose ? 'deterministic prose review' :
    author_route(pr, await pages(api,'/issues/'+number+'/comments'));
  const result = {number,base:pr.base.sha,head:pr.head.sha,prose,route};
  if (!await current()) return {...result,prose:false,route:'revision changed'};
  if (!canary || !writable) return result;

  // Only this installation can publish the check. A review is additionally bound to the PR and base.
  const reviews = await pages(api,path+'/reviews');
  const owned = reviews.filter(r => r.user.login === bot && r.user.type === 'Bot' && r.state === 'APPROVED');
  const valid = r => prose && r.commit_id === pr.head.sha && r.body === stamp;
  for (const review of owned.filter(r => !valid(r))) {
    await api(path+'/reviews/'+review.id+'/dismissals','PUT',{message:'Policy binding expired.'});
  }
  if (!await current()) return {...result,prose:false,route:'revision changed'};
  if (prose && !owned.some(valid)) {
    await api(path+'/reviews','POST',{commit_id:pr.head.sha,event:'APPROVE',body:stamp});
  }
  const checked = await current();
  const conclusion = checked && prose ? 'success' : 'action_required';
  const external_id = stamp;
  const summary = checked ? route : 'revision changed';
  const checks = await api('/commits/'+pr.head.sha+'/check-runs?check_name='+encodeURIComponent(check_name)+'&filter=latest&per_page=100');
  if (checks.total_count > checks.check_runs.length) throw Error('incomplete check runs');
  const prior = checks.check_runs.find(c => c.app.id === app_id && c.name === check_name);
  if (!prior || prior.external_id !== external_id || prior.conclusion !== conclusion) {
    const body = {name:check_name,external_id,status:'completed',conclusion,output:{title:check_name,summary}};
    if (prior) await api('/check-runs/'+prior.id,'PATCH',body);
    else await api('/check-runs','POST',{...body,head_sha:pr.head.sha});
  }
  if (!checked || !await current()) {
    // A base move retains the old head. Invalidate the successful check before returning.
    const runs = await api('/commits/'+pr.head.sha+'/check-runs?check_name='+encodeURIComponent(check_name));
    for (const run of runs.check_runs.filter(r => r.app.id === app_id && r.external_id === stamp)) {
      await api('/check-runs/'+run.id,'PATCH',{status:'completed',conclusion:'action_required'});
    }
    for (const r of await pages(api,path+'/reviews')) {
      if (r.user.login === bot && r.state === 'APPROVED' && r.body === stamp)
        await api(path+'/reviews/'+r.id+'/dismissals','PUT',{message:'Policy binding expired.'});
    }
    return {...result,prose:false,route:'revision changed'};
  }
  return result;
}
