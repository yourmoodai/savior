const marker = '<!-- savior-adversarial-review -->';

function verdict(report) {
  if (typeof report.body !== 'string' || !report.body.trim()) throw Error('review body is empty or missing');
  if (report.body.length > 2300) throw Error(`review body exceeds publication limit: ${report.body.length} characters`);
  if (/co-authored-by|generated-by|assisted-by|reviewed with|🤖/i.test(report.body)) throw Error('review body contains prohibited attribution');
  const clean = report.reporting_mode === 'deterministic' && report.reporting_complete === true && report.qualified === true && report.complete === true && !report.context_gaps?.length && report.disposition === 'APPROVE' && Array.isArray(report.findings) && report.findings.length === 0 && Array.isArray(report.review_failures) && report.review_failures.length === 0;
  if (!report.body.includes('**Verdict: '+(clean ? 'Approved' : 'Request changes')+'**')) throw Error('review body disagrees with computed verdict');
  return clean;
}

module.exports = async function publish({github, context, core, head, pr, phase, report, evidence, reason, quote}) {
  const args = {...context.repo, pull_number:Number(pr)};
  async function current() {
    const pull = (await github.rest.pulls.get(args)).data;
    return pull.state === 'open' && (phase === 'blocked' || !pull.draft) && pull.head.sha === head;
  }
  if (!await current() || (report && report.commit_id !== head)) {core.notice('stale review discarded'); return;}
  let clean = false;
  const runUrl = `${context.serverUrl || 'https://github.com'}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`;
  let content;
  if (phase === 'waiting') content = 'Checking review prerequisites and estimating cost before inference. Builds and tests start only after a complete approval.';
  else if (phase === 'failed') content = require('./status.cjs').render(reason || 'infrastructure_failed');
  else if (phase === 'blocked') content = require('./status.cjs').render(reason,quote,report?.budget);
  else if (phase === 'complete') {
    if (report.blocked_reason) content = require('./status.cjs').render(report.blocked_reason,report.quote,report.budget,report);
    else {clean = verdict(report); content = report.body;}
  }
  else throw Error('invalid publication phase');
  const tests = evidence || report?.test_evidence;
  const links = tests?.head === head ? (tests.runs || []).filter(run=>Number.isSafeInteger(run.id) && run.id>0).map(run=>`[Test run ${run.id}](${context.serverUrl || 'https://github.com'}/${context.repo.owner}/${context.repo.repo}/actions/runs/${run.id})`).join(' · ') : '';
  const body = `${marker}\nCommit: \`${head}\` · [CI evidence and review run](${runUrl})\n\n\n${content}`;
  if (body.length > 2500) throw Error('review comment exceeds 2500 characters');
  const comments = await github.paginate(github.rest.issues.listComments, {...context.repo, issue_number:Number(pr), per_page:100});
  const existing = comments.filter(comment => comment.user?.login === 'github-actions[bot]' && comment.user?.type === 'Bot' && comment.body?.startsWith(marker)).sort((a,b)=>a.id-b.id)[0];
  // Recheck after pagination; a new commit may have arrived during the API calls.
  if (!await current()) {core.notice('stale review discarded'); return;}
  if (existing) {
    if (existing.body !== body) await github.rest.issues.updateComment({...context.repo, comment_id:existing.id, body});
  } else await github.rest.issues.createComment({...context.repo, issue_number:Number(pr), body});
  if (report?.blocked_reason === 'cost_limit') {
    const notification = `<!-- savior-cost-notification:${head} -->`;
    if (!comments.some(c=>c.user?.login === 'github-actions[bot]' && c.user?.type === 'Bot' && c.body?.startsWith(notification))) {
      const pull = (await github.rest.pulls.get(args)).data;
      if (pull.base.sha !== report.quote.binding.base || !await current()) return;
      const owners = await require('./codeowners.cjs').owners({github,context,pr:pull});
      if (owners.length && await current()) await github.rest.issues.createComment({...context.repo,issue_number:Number(pr),
        body:`${notification}\n${owners.join(' ')}: automated review is blocked because the next-call reservation exceeds the remaining allowance. A repository admin can waive review for this revision with \`/savior review skip ${head}\`. See the review status for the reservation and allowances.`});
    }
  }
  if (phase === 'complete' && !report.blocked_reason) {
    const validated = report.reporting_mode === 'deterministic' && report.reporting_complete === true && report.qualified === true && report.complete === true && !report.context_gaps?.length && report.review_failures?.length === 0;
    const event = clean ? 'APPROVE' : validated && report.findings?.length > 0 ? 'REQUEST_CHANGES' : null;
    if (event) {
      const reviews = await github.paginate(github.rest.pulls.listReviews,{...args,per_page:100});
      const latest = reviews.filter(review=>review.user?.login==='github-actions[bot]' && review.user?.type==='Bot' && review.commit_id===head && ['APPROVED','CHANGES_REQUESTED'].includes(review.state)).at(-1);
      const state = event === 'APPROVE' ? 'APPROVED' : 'CHANGES_REQUESTED';
      const marker = `<!-- savior-assessment:${head}:${process.env.BASE_SHA}:${context.runId}:${process.env.GITHUB_RUN_ATTEMPT} -->`;
      if (latest?.state !== state || (clean && !latest.body?.startsWith(marker))) {
        if (!await current()) {core.notice('stale review discarded'); return;}
        await github.rest.pulls.createReview({...args,commit_id:head,event,body:marker+'\n'+'The complete qualified assessment is recorded in the single review status comment. '+(clean ? 'No validated defects were found.' : 'Validated defects require changes.')});
      }
    }
  }
  if (!['waiting','blocked'].includes(phase) && !clean) {
    const blocked_reason = phase === 'failed' ? reason || 'infrastructure_failed' : report?.blocked_reason;
    core.setFailed(blocked_reason ? require('./status.cjs').failure(blocked_reason, report)
      : 'adversarial review did not establish a clean result');
  }
  return clean;
};
module.exports.verdict = verdict;
