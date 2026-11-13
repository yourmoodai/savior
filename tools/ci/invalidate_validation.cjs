module.exports=async ({github,context})=>{
  const pull=context.payload.pull_request;
  if (pull && context.payload.action!=='dismissed' && context.payload.review?.state!=='changes_requested') return;
  const pulls=pull ? [pull] : await github.paginate(github.rest.pulls.list,{...context.repo,state:'open',base:'main',per_page:100});
  const runs=await github.paginate(github.rest.actions.listWorkflowRuns,{...context.repo,workflow_id:'pr-validation.yml',per_page:100});
  for (const pr of pulls) {
    await github.rest.repos.createCommitStatus({...context.repo,sha:pr.head.sha,context:'pr readiness',state:'failure',
      description:pull ? 'approval changed; review and validation required' : 'base changed; review and validation required'});
    for (const run of runs) {
      if (run.display_title===`pr validation #${pr.number}` && run.status!=='completed')
        await github.rest.actions.cancelWorkflowRun({...context.repo,run_id:run.id});
    }
    if (!pull && !pr.draft) await github.rest.actions.createWorkflowDispatch({...context.repo,
      workflow_id:'pr-validation.yml',ref:'main',inputs:{pr_number:String(pr.number)}});
  }
};
