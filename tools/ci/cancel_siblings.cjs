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

async function cancel_runs({github, context, core}, trigger, failed_check = false) {
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  const still_failed = run => run.id === trigger.id && run.run_attempt === trigger.run_attempt &&
    run.repository?.full_name === repository && eligible(run, trigger.head_sha, repository) &&
    ((run.status === 'completed' && failures.has(run.conclusion)) ||
      (failed_check && run.status === 'in_progress'));
  if (context.payload.repository?.full_name !== repository || !trigger || !still_failed(trigger)) return;
  // Events can arrive after a rerun has recovered. Never cancel against stale evidence.
  const latest = (await github.rest.actions.getWorkflowRun({...context.repo, run_id:trigger.id})).data;
  if (!still_failed(latest)) return;
  const runs = await github.paginate(github.rest.actions.listWorkflowRunsForRepo,
    {...context.repo, head_sha:trigger.head_sha, per_page:100});
  for (const candidate of runs) {
    if (candidate.id === trigger.id || !eligible(candidate, trigger.head_sha, repository) || candidate.status === 'completed') continue;
    const run = (await github.rest.actions.getWorkflowRun({...context.repo, run_id:candidate.id})).data;
    if (!eligible(run, trigger.head_sha, repository) || run.status === 'completed') continue;
    if (!still_failed((await github.rest.actions.getWorkflowRun({...context.repo, run_id:trigger.id})).data)) return;
    core.notice(`ci failure in run ${trigger.id}; cancelling sibling ${run.id}`);
    try { await github.rest.actions.cancelWorkflowRun({...context.repo, run_id:run.id}); }
    catch (error) { if (error.status !== 409) throw error; }
  }
}

module.exports = async function cancel_siblings(options) {
  const trigger = options.context.payload.workflow_run;
  if (trigger) await cancel_runs(options, trigger);
};

// This job runs before its own workflow completes, including before the
// workflow_run listener has been merged onto the default branch.
module.exports.from_failed_commit_check = async function from_failed_commit_check(options) {
  const {github, context, run_attempt} = options;
  if (!Number.isSafeInteger(run_attempt) || run_attempt < 1) return;
  const trigger = (await github.rest.actions.getWorkflowRun({...context.repo, run_id:context.runId})).data;
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  if (trigger.id !== context.runId || trigger.run_attempt !== run_attempt ||
      trigger.path !== '.github/workflows/commit-messages.yml' ||
      trigger.repository?.full_name !== repository || !eligible(trigger, trigger.head_sha, repository)) return;
  const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt,
    {...context.repo, run_id:trigger.id, attempt_number:run_attempt, per_page:100});
  if (!jobs.some(job => job.name === 'check' && job.run_id === trigger.id &&
      job.run_attempt === run_attempt && job.head_sha === trigger.head_sha &&
      job.status === 'completed' && failures.has(job.conclusion))) return;
  await cancel_runs(options, trigger, true);
};
module.exports.eligible = eligible;
