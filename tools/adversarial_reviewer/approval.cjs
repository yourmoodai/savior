const {execFileSync} = require('node:child_process');
const path = require('node:path');
const author_gate = require('./author_gate.cjs');

function parse(body) {
  if (typeof body !== 'string' || body.length > 200) throw Error('invalid_approval_command');
  const match = /^\/savior review approve ([a-f0-9]{64}) max-usd=((?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?)$/.exec(body.trim());
  if (!match) throw Error('invalid_approval_command');
  const [whole, fraction=''] = match[2].split('.');
  const amount = BigInt(whole)*1000000000n + BigInt(fraction.padEnd(2,'0'))*10000000n;
  if (amount <= 0n || amount > BigInt(Number.MAX_SAFE_INTEGER)) throw Error('invalid_approval_amount');
  return {quote_id:match[1], cap_nanodollars:Number(amount)};
}

async function authorized({github, context, pr, login, bootstrap=''}) {
  const {data} = await github.rest.repos.getCollaboratorPermissionLevel({...context.repo, username:login});
  if (data.permission === 'admin') return true;
  return author_gate({github,context,pr:{...pr,user:{login}},bootstrap});
}

function trusted_run(run, repository, branch) {
  return run.repository?.full_name === repository && run.path === '.github/workflows/adversarial-review.yml' &&
    (run.event === 'pull_request_target' ||
      (['push','workflow_dispatch','issue_comment'].includes(run.event) && [branch,'develop'].includes(run.head_branch)));
}

async function from_comment({github, context, pr, bootstrap='', decode}) {
  const original = context.payload.comment;
  if (context.eventName !== 'issue_comment' || context.payload.action !== 'created' ||
      !context.payload.issue?.pull_request || context.payload.issue.number !== pr.number ||
      !original || original.user?.type !== 'User') throw Error('invalid_approval_event');
  let comment;
  try { comment = (await github.rest.issues.getComment({...context.repo,comment_id:original.id})).data; }
  catch (error) { if (error.status === 404) throw Error('approval_deleted'); throw error; }
  if (comment.body !== original.body || comment.updated_at !== comment.created_at ||
      comment.user?.id !== original.user.id || comment.user?.type !== 'User') throw Error('approval_edited');
  if (!await authorized({github,context,pr,login:comment.user.login,bootstrap})) throw Error('approval_unauthorized');
  const request = parse(comment.body);
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  if (pr.state !== 'open' || pr.draft || pr.base.repo.full_name !== repository ||
      pr.base.ref !== context.payload.repository.default_branch) throw Error('pr_not_ready');
  const artifacts = await github.paginate(github.rest.actions.listArtifactsForRepo,{...context.repo,per_page:100});
  const artifact = artifacts.find(a=>a.name === 'review-quote-'+request.quote_id && !a.expired);
  if (!artifact || artifact.size_in_bytes > 1000000) throw Error('quote_unavailable');
  const run = (await github.rest.actions.getWorkflowRun({...context.repo,run_id:artifact.workflow_run.id})).data;
  if (!trusted_run(run,repository,context.payload.repository.default_branch)) throw Error('untrusted_quote');
  const archive = await github.rest.actions.downloadArtifact({...context.repo,artifact_id:artifact.id,archive_format:'zip'});
  let quote;
  try {
    quote = decode ? decode(Buffer.from(archive.data)) : JSON.parse(execFileSync('python3',
      [path.join(__dirname,'quote.py')],{input:Buffer.from(archive.data),maxBuffer:1000000,stdio:['pipe','pipe','pipe']}).toString());
  } catch { throw Error('quote_expired_or_invalid'); }
  if (quote.id !== request.quote_id || quote.binding.repository !== repository || quote.binding.pr !== pr.number ||
      quote.binding.head !== pr.head.sha || quote.binding.base !== pr.base.sha) throw Error('quote_stale');
  if (request.cap_nanodollars < quote.cap_nanodollars) throw Error('approval_below_estimate');
  return {quote, grant:{id:'comment-'+comment.id, quote_id:quote.id, repository, pr:pr.number,
    head:pr.head.sha, base:pr.base.sha, configuration:quote.binding.configuration,
    cap_nanodollars:request.cap_nanodollars, actor:comment.user.login, actor_id:comment.user.id, comment_id:comment.id,
    run_id:context.runId}};
}

async function revalidate({github,context,head,base,number,bootstrap=''}) {
  const pr = (await github.rest.pulls.get({...context.repo,pull_number:Number(number)})).data;
  if (pr.state !== 'open' || pr.draft || pr.base.ref !== context.payload.repository.default_branch) throw Error('pr_not_ready');
  if (pr.head.sha !== head || pr.base.sha !== base) throw Error('quote_stale');
  if (!await author_gate({github,context,pr,bootstrap})) throw Error('author_unauthorized');
  if (context.eventName === 'issue_comment') return from_comment({github,context,pr,bootstrap});
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

module.exports = {parse, authorized, from_comment, trusted_run, revalidate, record_failure};
