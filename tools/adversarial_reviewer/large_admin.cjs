const approval_body = 'LGTM. Large admin-authored PR; full assessment skipped under repository policy. This approval does not assert that code inspection or tests passed.';

function qualifies(pr, default_branch) {
  return pr.state === 'open' && !pr.draft && pr.base.ref === default_branch &&
    [pr.changed_files, pr.additions, pr.deletions].every(value => Number.isSafeInteger(value) && value >= 0) &&
    (pr.changed_files >= 100 || pr.additions + pr.deletions >= 10000);
}

async function is_admin(github, context, pr) {
  const {data} = await github.rest.repos.getCollaboratorPermissionLevel({
    ...context.repo, username:pr.user.login,
  });
  return pr.user.type === 'User' && data.permission === 'admin';
}

async function approve({github, context, core, pr}) {
  const default_branch = context.payload.repository.default_branch;
  if (!qualifies(pr, default_branch) || !await is_admin(github, context, pr)) return false;
  // A spending comment cannot grant a code approval or start inference here.
  if (context.eventName === 'issue_comment') {
    core.notice('large admin approval requires a pull request or manual workflow event');
    return true;
  }
  const args = {...context.repo, pull_number:pr.number};
  async function current() {
    const {data:pull} = await github.rest.pulls.get(args);
    return pull.head.sha === pr.head.sha && pull.base.sha === pr.base.sha &&
      pull.user.login === pr.user.login && qualifies(pull, default_branch) &&
      await is_admin(github, context, pull);
  }
  if (!await current()) {core.notice('stale maintainer approval discarded'); return true;}
  const reviews = await github.paginate(github.rest.pulls.listReviews,{...args,per_page:100});
  const latest = new Map();
  for (const review of reviews) {
    if (review.commit_id === pr.head.sha && ['APPROVED','CHANGES_REQUESTED','DISMISSED'].includes(review.state)) {
      latest.set(review.user.login,review);
    }
  }
  if ([...latest.values()].some(review => review.state === 'CHANGES_REQUESTED')) {
    core.setFailed('current revision has a request for changes; lightweight approval withheld');
    return true;
  }
  const existing = latest.get('github-actions[bot]');
  if (!await current()) {core.notice('stale maintainer approval discarded'); return true;}
  if (existing?.state !== 'APPROVED' || existing.user.type !== 'Bot') {
    await github.rest.pulls.createReview({...args,commit_id:pr.head.sha,event:'APPROVE',body:approval_body});
  }
  await core.summary.addRaw(`${approval_body}\n\n${pr.changed_files} changed files; ${pr.additions + pr.deletions} changed lines. No inference or spending reservation was requested.`).write();
  return true;
}

module.exports = {approve, qualifies};
