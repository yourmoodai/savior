const {createHash} = require('node:crypto');
const {trusted_run,verify_archive} = require('../adversarial_reviewer/approval.cjs');

const repository = 'yourmoodai/savior';
const repo_id = 1418413891;
const app_id = 5910949;
const bot = 'your-mood-savior-prose-review[bot]';
const check_name = 'Savior local code review';
const sha256 = text => createHash('sha256').update(text).digest('hex');

function cost_run(artifact,run,pr) {
  if (!artifact || artifact.expired || artifact.size_in_bytes > 1_000_000 ||
      artifact.workflow_run?.repository_id !== repo_id || run.id !== artifact.workflow_run.id ||
      !trusted_run(run,repository,'main') || !['workflow_dispatch','pull_request_target'].includes(run.event) ||
      run.path !== '.github/workflows/pr-validation.yml' || run.display_title !== 'pr validation #'+pr.number ||
      (run.event === 'workflow_dispatch' && run.head_branch !== 'main') || run.status !== 'completed')
    throw Error('trusted completed cost stop required');
}

function receipt_id(pr,artifact,config) {
  return ['v1',pr.number,pr.base.sha,pr.head.sha,artifact,config].join('/');
}

function marker(check) {
  return '<!-- savior-local-review:'+check.id+':'+sha256(check.output.text)+' -->';
}

async function validate_check({github,context,pr,id}) {
  if (context.eventName !== 'workflow_dispatch' || context.ref !== 'refs/heads/main' ||
      context.payload.repository.id !== repo_id || !/^[1-9]\d*$/.test(id)) throw Error('invalid local dispatch');
  const {data:check} = await github.rest.checks.get({...context.repo,check_run_id:Number(id)});
  const parts = check.external_id?.split('/') || [];
  const [, , , ,artifact_id,config] = parts;
  if (parts.length !== 6 || !/^[1-9]\d*$/.test(artifact_id) || !/^[a-f0-9]{64}$/.test(config) ||
      check.external_id !== receipt_id(pr,artifact_id,config) || check.id !== Number(id) ||
      check.app?.id !== app_id || check.name !== check_name || check.head_sha !== pr.head.sha ||
      check.status !== 'completed' || check.conclusion !== 'success' || !check.output?.text)
    throw Error('current successful local App check required');
  const {data:artifact} = await github.rest.actions.getArtifact({...context.repo,artifact_id:Number(artifact_id)});
  const {data:run} = await github.rest.actions.getWorkflowRun({...context.repo,run_id:artifact.workflow_run.id});
  cost_run(artifact,run,pr);
  const archive = await github.rest.actions.downloadArtifact({...context.repo,artifact_id:artifact.id,archive_format:'zip'});
  verify_archive(Buffer.from(archive.data),artifact,pr,repository);
  return marker(check);
}

module.exports = {repository,repo_id,app_id,bot,check_name,sha256,cost_run,receipt_id,marker,validate_check};
