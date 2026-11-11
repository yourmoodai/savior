const author_gate = require('./author_gate.cjs');
const publish = require('./publish.cjs');
const approvals = require('./approval.cjs');

module.exports = async function scope({github,context,core,enabled,bootstrap=''}) {
  let number = context.payload.pull_request?.number || context.payload.issue?.number || context.payload.inputs?.pr_number;
  if (context.eventName === 'push') {
    const pulls = (await github.rest.pulls.list({...context.repo,state:'open',head:context.repo.owner+':develop',base:'main'})).data;
    if (pulls.length !== 1) throw Error('expected one open develop PR');
    number = pulls[0].number;
  }
  if (!/^[1-9][0-9]*$/.test(String(number))) throw Error('invalid PR');
  const pr = (await github.rest.pulls.get({...context.repo,pull_number:Number(number)})).data;
  core.setOutput('eligible','false');
  core.setOutput('pr',pr.number); core.setOutput('head',pr.head.sha); core.setOutput('base',pr.base.sha);
  let reason = pr.state !== 'open' || pr.base.ref !== context.payload.repository.default_branch ? 'pr_not_ready' : pr.draft ? 'draft' : null;
  try {
    if (!reason && await require('./large_admin.cjs').approve({github,context,core,pr})) return;
    if (!reason && !await author_gate({github,context,pr,bootstrap})) reason = 'author_unauthorized';
    if (!reason && enabled !== 'true') reason = 'disabled';
    if (!reason && context.eventName === 'issue_comment') await approvals.from_comment({github,context,pr,bootstrap});
  } catch (error) {
    reason = Object.hasOwn(require('./status.cjs').reasons,error.message) ? error.message : 'infrastructure_failed';
  }
  if (reason) {
    core.notice(reason);
    // Untrusted comments cannot replace the current review result.
    if (context.eventName === 'issue_comment' && ['approval_unauthorized','invalid_approval_event'].includes(reason)) {
      await core.summary.addRaw(require('./status.cjs').render(reason)).write();
      return;
    }
    await publish({github,context,core,head:pr.head.sha,pr:pr.number,phase:'blocked',reason});
    return;
  }
  core.setOutput('eligible','true');
};
