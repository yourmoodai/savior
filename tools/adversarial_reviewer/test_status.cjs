const required = ['verify.yml', 'commit-messages.yml', 'qualification.yml'];

function select_runs(runs, head, pull, repository) {
  return required.map(file => {
    const matching = runs.filter(run => run.head_sha === head && run.path === '.github/workflows/' + file &&
      (run.event === 'pull_request' ||
       (run.event === 'workflow_dispatch' && pull?.head.ref?.startsWith('release/v') &&
        run.head_branch === pull.head.ref && pull.head.repo?.full_name === repository &&
        run.head_repository?.full_name === repository)));
    return matching.sort((a, b) => b.id - a.id)[0];
  });
}

async function collect(pull, head, repository, runs, list_jobs, output) {
  if (pull.state !== 'open' || pull.head.sha !== head) throw Error('review target is stale');
  const evidence = {head, observed_at:new Date().toISOString(), runs:[]};
  for (const [index, run] of select_runs(runs, head, pull, repository).entries()) {
    if (!run) {
      evidence.runs.push({workflow:'.github/workflows/'+required[index], status:'not_started', conclusion:null, jobs:[]});
      continue;
    }
    const jobs = await list_jobs(run);
    evidence.runs.push({workflow:run.path, id:run.id, attempt:run.run_attempt, status:run.status, conclusion:run.conclusion,
      jobs:jobs.map(job=>({name:job.name, status:job.status, conclusion:job.conclusion,
        steps:(job.steps || []).map(step=>({name:step.name, status:step.status, conclusion:step.conclusion}))}))});
  }
  require('node:fs').writeFileSync(output, JSON.stringify(evidence));
}

module.exports = async function snapshot({github, context, head, pr, output}) {
  const pull = (await github.rest.pulls.get({...context.repo, pull_number:Number(pr)})).data;
  if (pull.draft) throw Error('review target is stale');
  const runs = await github.paginate(github.rest.actions.listWorkflowRunsForRepo, {...context.repo, head_sha:head, per_page:100});
  const jobs = run => github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt,
    {...context.repo, run_id:run.id, attempt_number:run.run_attempt, per_page:100});
  await collect(pull, head, `${context.repo.owner}/${context.repo.repo}`, runs, jobs, output);
};
module.exports.select_runs = select_runs;

if (require.main === module) {
  const [repository, number, head, output] = process.argv.slice(2);
  if (repository !== 'yourmoodai/savior' || !/^[1-9]\d*$/.test(number) || !/^[a-f0-9]{40}$/.test(head))
    throw Error('invalid host review target');
  function api(path, paginate = false) {
    const args = ['api', '--hostname', 'github.com', 'repos/'+repository+path];
    if (paginate) args.push('--paginate', '--slurp');
    return JSON.parse(require('node:child_process').execFileSync('gh', args, {timeout:60000, maxBuffer:16*1024*1024})); // 16 MiB
  }
  const pull = api('/pulls/'+number);
  const runs = api('/actions/runs?head_sha='+head+'&per_page=100', true).flatMap(page => page.workflow_runs);
  const jobs = run => api(`/actions/runs/${run.id}/attempts/${run.run_attempt}/jobs?per_page=100`, true).flatMap(page => page.jobs);
  collect(pull, head, repository, runs, jobs, output).catch(error => {console.error(error.message); process.exitCode = 1;});
}
