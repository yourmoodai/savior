const paths = new Map([
  ['verify.yml', 'verify'],
  ['commit-messages.yml', 'commit messages'],
  ['qualification.yml', 'release qualification'],
  ['adversarial-review.yml', 'adversarial code review'],
].map(([file, name]) => ['.github/workflows/' + file, name]));
const failures = new Set(['failure', 'timed_out', 'action_required', 'startup_failure']);

function eligible(run, head, repository) {
  return run.head_sha === head && paths.has(run.path) && paths.get(run.path) === run.name &&
    run.head_repository?.full_name === repository &&
    // pull_request_target head_sha identifies trusted base code, not the reviewed change.
    (run.event === 'pull_request' ||
      (run.event === 'push' && ['main', 'develop'].includes(run.head_branch)) ||
      (run.event === 'workflow_dispatch' &&
        (['main', 'develop'].includes(run.head_branch) || run.head_branch?.startsWith('release/v'))));
}

module.exports = async function cancel_siblings({github, context, core}) {
  const trigger = context.payload.workflow_run;
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  if (context.payload.repository?.full_name !== repository || !trigger ||
      trigger.repository?.full_name !== repository || trigger.status !== 'completed' ||
      !failures.has(trigger.conclusion) ||
      !eligible(trigger, trigger.head_sha, repository)) return;
  // Events can arrive after a rerun has recovered. Never cancel against stale evidence.
  const latest = (await github.rest.actions.getWorkflowRun({...context.repo, run_id:trigger.id})).data;
  if (latest.run_attempt !== trigger.run_attempt || latest.status !== 'completed' ||
      !failures.has(latest.conclusion) || !eligible(latest, trigger.head_sha, repository)) return;
  const runs = await github.paginate(github.rest.actions.listWorkflowRunsForRepo,
    {...context.repo, head_sha:trigger.head_sha, per_page:100});
  for (const candidate of runs) {
    if (!eligible(candidate, trigger.head_sha, repository) || candidate.status === 'completed') continue;
    const run = (await github.rest.actions.getWorkflowRun({...context.repo, run_id:candidate.id})).data;
    if (!eligible(run, trigger.head_sha, repository) || run.status === 'completed') continue;
    core.notice(`ci failure in run ${trigger.id}; cancelling sibling ${run.id}`);
    try { await github.rest.actions.cancelWorkflowRun({...context.repo, run_id:run.id}); }
    catch (error) { if (error.status !== 409) throw error; }
  }
};
module.exports.eligible = eligible;
