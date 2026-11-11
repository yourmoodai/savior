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

module.exports = async function snapshot({github, context, head, pr, output}) {
  const pull = (await github.rest.pulls.get({...context.repo, pull_number:Number(pr)})).data;
  if (pull.state !== 'open' || pull.draft || pull.head.sha !== head) throw Error('review target is stale');
  const runs = await github.paginate(github.rest.actions.listWorkflowRunsForRepo, {...context.repo, head_sha:head, per_page:100});
  const evidence = {head, observed_at:new Date().toISOString(), runs:[]};
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  for (const [index, run] of select_runs(runs, head, pull, repository).entries()) {
    if (!run) {
      evidence.runs.push({workflow:'.github/workflows/'+required[index], status:'not_started', conclusion:null, jobs:[]});
      continue;
    }
    const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {...context.repo, run_id:run.id, attempt_number:run.run_attempt, per_page:100});
    evidence.runs.push({workflow:run.path, id:run.id, attempt:run.run_attempt, status:run.status, conclusion:run.conclusion,
      jobs:jobs.map(job=>({name:job.name, status:job.status, conclusion:job.conclusion,
        steps:(job.steps || []).map(step=>({name:step.name, status:step.status, conclusion:step.conclusion}))}))});
  }
  require('node:fs').writeFileSync(output, JSON.stringify(evidence));
};
module.exports.select_runs = select_runs;
