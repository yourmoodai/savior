const context_name = 'pr readiness';

function assert_target(pr, binding) {
  if (pr.state !== 'open' || pr.draft || pr.head.sha !== binding.head ||
      pr.base.sha !== binding.base || pr.base.ref !== 'main') throw Error('approved revision changed');
}

function assert_approval(reviews, binding) {
  const latest = new Map();
  for (const r of [...reviews].sort((a,b)=>a.id-b.id)) {
    if (['APPROVED','CHANGES_REQUESTED','DISMISSED'].includes(r.state)) latest.set(r.user.login,r);
  }
  if ([...latest.values()].some(r=>r.state==='CHANGES_REQUESTED')) throw Error('changes requested');
  const r=latest.get('github-actions[bot]');
  const marker=`<!-- savior-assessment:${binding.head}:${binding.base}:${binding.run}:${binding.attempt} -->`;
  if (r?.user.type !== 'Bot' || r.state !== 'APPROVED' || r.commit_id !== binding.head ||
      !r.body?.startsWith(marker)) throw Error('current adversarial approval required');
}

async function execute({github,context,core,phase,binding,results}) {
  const number=Number(binding?.pr || context.payload.pull_request?.number || context.payload.issue?.number || context.payload.inputs?.pr_number);
  if (!Number.isSafeInteger(number) || number<=0) throw Error('invalid pr');
  const args={...context.repo,pull_number:number};
  const pr=(await github.rest.pulls.get(args)).data;
  const target=binding || {pr:String(number),head:pr.head.sha,base:pr.base.sha};
  const run_url=`${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}/attempts/${target.attempt || process.env.GITHUB_RUN_ATTEMPT}`;
  async function status(state,description) {
    if (phase==='finish') {
      const {data}=await github.rest.repos.getCombinedStatusForRef({...context.repo,ref:target.head,per_page:100});
      const current=data.statuses.find(s=>s.context===context_name);
      if (current?.target_url!==run_url)
        throw Error(`validation status superseded: expected ${run_url}; current ${current?.target_url || 'missing'}`);
    }
    for (const name of ['pr verification','pr qualification','pr site','pr commits',context_name])
      await github.rest.repos.createCommitStatus({...context.repo,sha:target.head,context:name,state,description,
        target_url:run_url});
  }
  if (phase==='start') {
    for (const [key,value] of Object.entries(target)) core.setOutput(key,value);
    await status('pending','waiting for adversarial approval and validation');
    return;
  }
  try {
    assert_target(pr,target);
    const reviews=await github.paginate(github.rest.pulls.listReviews,{...args,per_page:100});
    assert_approval(reviews,target);
    if (phase==='finish') {
      if (!results || !['review','admission','commits','verify','qualification'].every(k=>results[k]?.result==='success') ||
          results.site?.result !== (results.admission.outputs.site_required==='true' ? 'success' : 'skipped'))
        throw Error('review or downstream validation failed');
      assert_target((await github.rest.pulls.get(args)).data,target);
      await status('success','approved revision passed all applicable validation');
      return;
    }
    const merge=(await github.rest.git.getCommit({...context.repo,commit_sha:pr.merge_commit_sha})).data;
    if (merge.parents.length!==2 || merge.parents[0].sha!==target.base || merge.parents[1].sha!==target.head)
      throw Error('merge revision does not match approval');
    assert_target((await github.rest.pulls.get(args)).data,target);
    const files=await github.paginate(github.rest.pulls.listFiles,{...args,per_page:100});
    const site_path=p=>p.startsWith('site/') || /^package.*\.json$/.test(p) ||
      ['scripts/site.py','scripts/test-site.mjs','docs/assets/savior.svg','docs/assets/social-preview.png','.github/workflows/site.yml'].includes(p);
    core.setOutput('site_required',String(files.length>=3000 || files.some(f=>site_path(f.filename) || (f.previous_filename && site_path(f.previous_filename)))));
    assert_target((await github.rest.pulls.get(args)).data,target);
    core.setOutput('test_sha',merge.sha);
  } catch(error) {
    if (phase==='finish') await status('failure',String(error.message).slice(0,140));
    throw error;
  }
}
module.exports={execute,assert_target,assert_approval};
