const {execFileSync} = require('node:child_process');
const path = require('node:path');
const author_gate = require('./author_gate.cjs');
const {usd} = require('./status.cjs');

function parse(body) {
  if (typeof body !== 'string' || body.length > 100) throw Error('invalid_approval_command');
  const match = /^\/savior review skip ([a-f0-9]{40})$/.exec(body.trim());
  if (!match) throw Error('invalid_approval_command');
  return {head:match[1]};
}

function trusted_run(run, repository, branch) {
  return run.repository?.full_name === repository && run.path === '.github/workflows/adversarial-review.yml' &&
    (run.event === 'pull_request_target' ||
      (['push','workflow_dispatch','issue_comment'].includes(run.event) && [branch,'develop'].includes(run.head_branch)));
}

async function comment_authority({github,context,pr}) {
  const original = context.payload.comment;
  if (context.eventName !== 'issue_comment' || context.payload.action !== 'created' ||
      !context.payload.issue?.pull_request || context.payload.issue.number !== pr.number ||
      !original || original.user?.type !== 'User') throw Error('invalid_approval_event');
  let comment;
  try { comment = (await github.rest.issues.getComment({...context.repo,comment_id:original.id})).data; }
  catch (error) { if (error.status === 404) throw Error('approval_deleted'); throw error; }
  if (comment.body !== original.body || comment.updated_at !== comment.created_at ||
      comment.user?.id !== original.user.id || comment.user?.type !== 'User' ||
      comment.user.login !== original.user.login) throw Error('approval_edited');
  const {data} = await github.rest.repos.getCollaboratorPermissionLevel({...context.repo,username:comment.user.login});
  if (data.permission !== 'admin') throw Error('approval_unauthorized');
  if (parse(comment.body).head !== pr.head.sha) throw Error('quote_stale');
  return comment;
}

function ready(pr, context) {
  if (pr.state !== 'open' || pr.draft || pr.base.repo.full_name !== `${context.repo.owner}/${context.repo.repo}` ||
      pr.base.ref !== context.payload.repository.default_branch) throw Error('pr_not_ready');
}

function cost_block(quote, pr, repository) {
  const p = quote.policy;
  if (quote.schema !== 3 || quote.projection_informational !== true ||
      quote.blocked_reason !== 'cost_limit' || quote.binding?.repository !== repository ||
      quote.binding.pr !== pr.number || quote.binding.head !== pr.head.sha || quote.binding.base !== pr.base.sha ||
      !p || p.repository !== repository.toLowerCase() || p.pr !== pr.number ||
      p.pr_limit_nanodollars !== 2e9 || p.daily_limit_nanodollars !== 5e9 ||
      ![quote.next_call_reserved_nanodollars,quote.projected_nanodollars,p.pr_remaining_nanodollars,p.daily_remaining_nanodollars].every(n=>Number.isSafeInteger(n) && n>=0) ||
      p.pr_remaining_nanodollars > 2e9 || p.daily_remaining_nanodollars > 5e9 ||
      quote.next_call_reserved_nanodollars <= Math.min(p.pr_remaining_nanodollars,p.daily_remaining_nanodollars)) throw Error('quote_stale');
}

async function from_comment({github,context,core,pr,decode}) {
  ready(pr,context);
  const comment = await comment_authority({github,context,pr});
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  const artifacts = await github.paginate(github.rest.actions.listArtifactsForRepo,{...context.repo,per_page:100});
  const artifact = artifacts.filter(a=>a.name.startsWith(`review-cost-block-${pr.number}-${pr.head.sha}-`) && !a.expired)
    .sort((a,b)=>b.id-a.id)[0];
  if (!artifact || artifact.size_in_bytes > 1000000) throw Error('quote_unavailable');
  const run = (await github.rest.actions.getWorkflowRun({...context.repo,run_id:artifact.workflow_run.id})).data;
  if (!trusted_run(run,repository,context.payload.repository.default_branch)) throw Error('untrusted_quote');
  const archive = await github.rest.actions.downloadArtifact({...context.repo,artifact_id:artifact.id,archive_format:'zip'});
  let quote;
  try {
    quote = decode ? decode(Buffer.from(archive.data)) : JSON.parse(execFileSync('python3',
      [path.join(__dirname,'quote.py')],{input:Buffer.from(archive.data),maxBuffer:1000000,stdio:['pipe','pipe','pipe']}).toString());
  } catch { throw Error('quote_expired_or_invalid'); }
  cost_block(quote,pr,repository);
  const args = {...context.repo,pull_number:pr.number};
  async function recheck() {
    const pull = (await github.rest.pulls.get(args)).data;
    ready(pull,context);
    if (pull.head.sha !== pr.head.sha || pull.base.sha !== pr.base.sha) throw Error('quote_stale');
    await comment_authority({github,context,pr:pull});
    const reviews = await github.paginate(github.rest.pulls.listReviews,{...args,per_page:100});
    const latest = new Map();
    for (const review of reviews) {
      if (['APPROVED','CHANGES_REQUESTED','DISMISSED'].includes(review.state)) latest.set(review.user.login,review);
    }
    // Requests on earlier revisions remain binding until superseded or dismissed.
    if ([...latest.values()].some(r=>r.state === 'CHANGES_REQUESTED')) throw Error('changes_requested');
    const final_pull = (await github.rest.pulls.get(args)).data;
    ready(final_pull,context);
    if (final_pull.head.sha !== pr.head.sha || final_pull.base.sha !== pr.base.sha) throw Error('quote_stale');
    return latest.get('github-actions[bot]');
  }
  await recheck();
  const existing = await recheck();
  const marker = `<!-- savior-cost-waiver:${pr.head.sha}:${pr.base.sha} -->`;
  const body = `${marker}\nLGTM — automated review waived by @${comment.user.login} because the next-call reservation exceeds the remaining allowance. No automated code-review pass is asserted.\n\n`+
    `Authorization: ${context.serverUrl || 'https://github.com'}/${repository}/pull/${pr.number}#issuecomment-${comment.id}\n`+
    `Head: \`${pr.head.sha}\`; base: \`${pr.base.sha}\`; cost evidence: \`${quote.id}\`.\n`+
    `Next-call reservation: ${usd(quote.next_call_reserved_nanodollars,Math.ceil)}. `+
    `Whole-plan worst-case projection (informational): ${usd(quote.projected_nanodollars,Math.ceil)}. `+
    `PR remaining: ${usd(quote.policy.pr_remaining_nanodollars)} of $2.00; `+
    `daily remaining: ${usd(quote.policy.daily_remaining_nanodollars)} of $5.00 (${quote.policy.utc_day} UTC).\n`+
    `Actor ID: ${comment.user.id}. Other reviews and required checks still apply.`;
  if (!(existing?.state === 'APPROVED' && existing.user.type === 'Bot' && existing.commit_id === pr.head.sha && existing.body?.startsWith(marker))) {
    await github.rest.pulls.createReview({...args,commit_id:pr.head.sha,event:'APPROVE',body});
  }
  await core.summary.addRaw(body).write();
  return {quote,comment_id:comment.id};
}

async function revalidate({github,context,head,base,number,bootstrap=''}) {
  const pr = (await github.rest.pulls.get({...context.repo,pull_number:Number(number)})).data;
  ready(pr,context);
  if (pr.head.sha !== head || pr.base.sha !== base) throw Error('quote_stale');
  if (!await author_gate({github,context,pr,bootstrap})) throw Error('author_unauthorized');
}

async function record_failure(action) {
  try { return await action(); }
  catch (error) {
    const fs = require('node:fs');
    fs.mkdirSync('review-result',{recursive:true});
    const reason = Object.hasOwn(require('./status.cjs').reasons,error.message) ? error.message : 'infrastructure_failed';
    fs.writeFileSync('review-result/failure.json',JSON.stringify({reason}));
    throw error;
  }
}

module.exports = {parse,from_comment,trusted_run,revalidate,record_failure,cost_block};
