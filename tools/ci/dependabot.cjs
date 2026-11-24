function is_dependabot(pr, repository) {
  return Boolean(repository) && pr.user?.type === 'Bot' && pr.user.login === 'dependabot[bot]' &&
    pr.base.ref === 'main' && pr.base.repo.full_name === repository &&
    pr.head.repo?.full_name === repository;
}

function signed_update(commit) {
  if (!commit.commit.verification.verified) return false;
  if (commit.author?.login === 'dependabot[bot]') return true;
  return commit.author?.login === 'github-actions[bot]' && commit.committer?.login === 'web-flow' &&
    commit.parents.length === 2;
}

async function candidate({github, context}, number) {
  const args = {...context.repo, pull_number: number};
  const repository = context.payload.repository.full_name;
  const {data: pr} = await github.rest.pulls.get(args);
  if (!is_dependabot(pr, repository) || pr.state !== 'open' || pr.draft) {
    throw Error('expected an open dependabot pull request');
  }
  const main = (await github.rest.git.getRef({...context.repo, ref: 'heads/main'})).data.object.sha;
  if (pr.base.sha !== main) throw Error('base revision changed');
  const commits = await github.paginate(github.rest.pulls.listCommits, {...args, per_page: 100});
  if (!commits.length || commits.length !== pr.commits || commits.at(-1).sha !== pr.head.sha ||
      commits.some(commit => !signed_update(commit))) {
    throw Error('expected signed dependabot commits');
  }
  for (const commit of commits.filter(c => c.author.login === 'github-actions[bot]')) {
    const {data: comparison} = await github.rest.repos.compareCommitsWithBasehead({...context.repo,
      basehead: commit.parents[1].sha + '...' + main});
    if (!['ahead', 'identical'].includes(comparison.status)) throw Error('unexpected branch update parent');
  }
  const {data: merge} = await github.rest.git.getCommit({...context.repo, commit_sha: pr.merge_commit_sha});
  if (merge.parents.length !== 2 || merge.parents[0].sha !== pr.base.sha || merge.parents[1].sha !== pr.head.sha) {
    throw Error('merge candidate changed');
  }
  return {pr: String(number), base: pr.base.sha, head: pr.head.sha, test_sha: merge.sha, tree: merge.tree.sha};
}

function requested_pr(context) {
  const run = context.payload.workflow_run;
  const match = /^dependabot build #([1-9]\d*)$/.exec(run.display_title);
  if (!match || run.path !== '.github/workflows/dependabot-build.yml' || run.conclusion !== 'success' ||
      !['workflow_dispatch', 'pull_request'].includes(run.event) ||
      run.head_repository?.full_name !== context.payload.repository.full_name ||
      run.repository.full_name !== context.payload.repository.full_name) {
    throw Error('unexpected validation workflow');
  }
  return Number(match[1]);
}

async function merge_run(options) {
  const {github, context} = options;
  const number = requested_pr(context);
  const run = context.payload.workflow_run;
  const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
    ...context.repo, run_id: run.id, attempt_number: run.run_attempt, per_page: 100
  });
  const expected = await candidate(options, number);
  const succeeded = name => jobs.some(job => job.name === name && job.conclusion === 'success');
  if (run.head_sha !== expected.head || !succeeded('verify / verification') ||
      !succeeded(`build ${expected.base} ${expected.head} ${expected.tree}`)) {
    throw Error('current build and verification required');
  }

  const args = {...context.repo, pull_number: number};
  async function check_revision() {
    const current = await candidate(options, number);
    // GitHub can regenerate the synthetic commit while preserving its parents and tree.
    if (['pr', 'base', 'head', 'tree'].some(key => current[key] !== expected[key])) {
      throw Error('tested revision changed');
    }
  }
  await check_revision();
  await github.rest.pulls.createReview({...args, event: 'APPROVE', commit_id: expected.head});
  await check_revision();
  const {data} = await github.rest.pulls.merge({...args, sha: expected.head, merge_method: 'squash'});
  if (!data.merged) throw Error('dependabot merge failed');
  // A push made with GITHUB_TOKEN does not trigger the base-change listener.
  await github.rest.actions.createWorkflowDispatch({...context.repo, workflow_id: 'invalidate-validation.yml', ref: 'main'});
}

module.exports = {is_dependabot, requested_pr, candidate, merge_run};
