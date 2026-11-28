const {createHash} = require('node:crypto');
const {trusted_run,verify_archive,latest_reviews} = require('../adversarial_reviewer/approval.cjs');

const repository = 'yourmoodai/savior';
const repo_id = 1418413891;
const app_id = 5910949;
const bot_id = 360859138;
const check_name = 'Savior local code review';
const sha256 = text => createHash('sha256').update(text).digest('hex');

function is_review_bot(user) {
  return user?.type === 'Bot' && user.id === bot_id;
}

function run_base(artifact,run,pr) {
  if (artifact.workflow_run?.repository_id!==repo_id || run.id!==artifact.workflow_run.id ||
      !trusted_run(run,repository,'main') || !['workflow_dispatch','pull_request_target'].includes(run.event) ||
      run.path!=='.github/workflows/pr-validation.yml' || run.display_title!=='pr validation #'+pr.number ||
      run.status!=='completed') return null;
  if (run.event==='workflow_dispatch') return run.head_branch==='main' ? run.head_sha : null;
  const target=run.pull_requests?.find(p=>p.number===pr.number && p.base.ref==='main' &&
    p.base.repo.id===repo_id && p.head.repo?.id===repo_id && p.head.sha===pr.head.sha);
  return target && [target.base.sha,target.head.sha].includes(run.head_sha) ? target.base.sha : null;
}

function cost_run(artifact,run,pr) {
  const base=run_base(artifact,run,pr);
  if (!base || artifact.expired || artifact.size_in_bytes>1_000_000) throw Error('trusted completed cost stop required');
  return base;
}

function receipt_id(pr,artifact,config) {
  return ['v1',pr.number,pr.base.sha,pr.head.sha,artifact,config].join('/');
}

function receipt_binding(value) {
  const match=/^v1\/([1-9]\d*)\/([a-f0-9]{40})\/([a-f0-9]{40})\/([1-9]\d*)\/([a-f0-9]{64})$/.exec(value);
  if (!match) throw Error('invalid local review receipt');
  return {pr:Number(match[1]),base:match[2],head:match[3],artifact_id:Number(match[4])};
}

function marker(check) {
  return '<!-- savior-local-review:'+check.id+':'+sha256(check.output.text)+' -->';
}

function assert_no_conflict(checks,reviews,pr) {
  const prefix=['v1',pr.number,pr.base.sha,pr.head.sha,''].join('/');
  for (const check of checks) {
    if (check.app?.id!==app_id || check.name!==check_name || check.head_sha!==pr.head.sha ||
        check.conclusion!=='failure' || !check.external_id?.startsWith(prefix)) continue;
    const dismissed=reviews.some(r=>r.state==='DISMISSED' && is_review_bot(r.user) &&
      r.commit_id===pr.head.sha && r.body?.startsWith(marker(check)));
    if (!dismissed) throw Error('conflicting local review requires resolution');
  }
}

async function validate_check({github,context,pr,id}) {
  if (context.eventName !== 'workflow_dispatch' || context.ref !== 'refs/heads/main' ||
      context.payload.repository.id !== repo_id || !/^[1-9]\d*$/.test(id)) throw Error('invalid local dispatch');
  const {data:check} = await github.rest.checks.get({...context.repo,check_run_id:Number(id)});
  const binding=receipt_binding(check.external_id);
  const artifact_id=binding.artifact_id;
  if (binding.pr!==pr.number || binding.base!==pr.base.sha || binding.head!==pr.head.sha || check.id!==Number(id) ||
      check.app?.id !== app_id || check.name !== check_name || check.head_sha !== pr.head.sha ||
      check.status !== 'completed' || check.conclusion !== 'success' || !check.output?.text)
    throw Error('current successful local App check required');
  const {data:artifact} = await github.rest.actions.getArtifact({...context.repo,artifact_id:Number(artifact_id)});
  const {data:run} = await github.rest.actions.getWorkflowRun({...context.repo,run_id:artifact.workflow_run.id});
  if (cost_run(artifact,run,pr)!==pr.base.sha) throw Error('stale budget-stop workflow');
  const archive = await github.rest.actions.downloadArtifact({...context.repo,artifact_id:artifact.id,archive_format:'zip'});
  verify_archive(Buffer.from(archive.data),artifact,pr,repository);
  return marker(check);
}

module.exports = {repository,repo_id,app_id,is_review_bot,latest_reviews,check_name,sha256,run_base,cost_run,receipt_id,receipt_binding,marker,assert_no_conflict,validate_check};
