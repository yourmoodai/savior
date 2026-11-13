const reasons = {
  draft:'Review not performed: this PR is a draft. Mark it ready for review to start the eligibility checks.',
  author_unauthorized:'Review not performed: the PR author is neither a repository admin nor a named CODEOWNER with write access.',
  disabled:'Review not performed: the Bedrock reviewer is disabled.',
  spending_unresolved:'Review blocked: earlier spending is missing or unattributed. Reconcile the ledger before new inference. A waiver cannot repair spending evidence.',
  cost_limit:'Automated review blocked by cost.',
  budget_exhausted:'Review stopped at its spending limit. Coverage is incomplete.',
  qualification_failed:'Review not performed: live model qualification failed.',
  infrastructure_failed:'Review unavailable: an infrastructure prerequisite failed. Inspect the linked workflow; this is not a code finding.',
  quote_stale:'Waiver rejected: the revision or cost evidence changed. Request a fresh review run.',
  quote_unavailable:'Waiver rejected: no current cost-block artifact is available. Request a fresh review run.',
  untrusted_quote:'Waiver rejected: cost evidence did not come from the trusted review workflow.',
  invalid_approval_event:'Waiver rejected: only a newly created human PR comment can waive review.',
  unsupported_pricing:'Review blocked: model pricing is missing, invalid, or older than 90 days.',
  quote_expired_or_invalid:'Waiver rejected: cost evidence is expired or invalid. Request a fresh review run.',
  approval_edited:'Waiver rejected: comments must be newly created and unedited.',
  approval_deleted:'Waiver rejected: the comment was deleted before execution.',
  approval_unauthorized:'Waiver rejected: only repository admins can waive automated review.',
  invalid_approval_command:'Waiver rejected: use /savior review skip <full-head-sha>. Comments cannot increase spending limits.',
  changes_requested:'Waiver rejected: an outstanding request for changes must be resolved or dismissed by an authorized reviewer.',
  pr_not_ready:'Review not performed: the PR must be open, ready for review, and target the default branch.',
};
const usd = (n, round=Math.floor) => '$'+(round(n/1e7)/100).toFixed(2);
function render(reason, quote, budget, report) {
  const lines = [reasons[reason] || reasons.infrastructure_failed];
  if (reason === 'cost_limit' && quote?.policy) {
    lines.push(`Next-call reservation: ${usd(quote.next_call_reserved_nanodollars,Math.ceil)}`,
      `Whole-plan worst-case projection (informational): ${usd(quote.projected_nanodollars,Math.ceil)}`,
      `PR allowance remaining: ${usd(quote.policy.pr_remaining_nanodollars)} of $2.00`,
      `Daily allowance remaining: ${usd(quote.policy.daily_remaining_nanodollars)} of $5.00`,
      'Daily reset: midnight UTC. The PR allowance does not reset.',
      '', 'A repository admin can waive automated review for this revision using:',
      '```', `/savior review skip ${quote.binding.head}`, '```',
      'The waiver submits an approval without asserting a code-review pass. Other reviews and required checks still apply.');
  }
  if (Number.isInteger(report?.total_chunks)) lines.push(`Coverage: ${report.completed_chunks || 0}/${report.total_chunks} chunks. Partial results remain in the report artifact.`);
  return lines.join('\n');
}
module.exports = {render,reasons,usd};
