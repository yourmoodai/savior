const reasons = {
  draft:'Review not performed: this PR is a draft. Mark it ready for review to start the eligibility checks.',
  author_unauthorized:'Review not performed: the PR author is neither a repository admin nor a named CODEOWNER with write access.',
  disabled:'Review not performed: the Bedrock reviewer is disabled. A spending approval cannot enable it.',
  spending_unresolved:'Review blocked: earlier spending is missing or unreconciled. Restore the ledger evidence before approving any new spending.',
  awaiting_budget:'Review is awaiting spending approval. No new inference has started.',
  budget_exhausted:'Review stopped at its spending limit. Coverage is incomplete; the quote below covers a new attempt using reusable results.',
  qualification_failed:'Review not performed: live model qualification failed. Extra spending does not waive qualification.',
  infrastructure_failed:'Review unavailable: an infrastructure prerequisite failed. Inspect the linked workflow; this is not a code finding.',
  quote_stale:'Approval rejected: the commit, configuration, or plan changed. Use a current quote; request a fresh review run if none is shown.',
  quote_unavailable:'Approval rejected: the quote artifact is missing or expired. Request a fresh review run.',
  untrusted_quote:'Approval rejected: the quote did not come from the trusted review workflow.',
  invalid_approval_event:'Approval rejected: only a newly created human PR comment can authorize spending.',
  unsupported_pricing:'Review blocked: the configured model or region has no verified pricing in this reviewer.',
  quote_expired_or_invalid:'Approval rejected: the quote is expired or invalid. Request a fresh review run.',
  approval_below_estimate:'Approval rejected: its cap is below the quoted reservation. Use the quoted amount or request a new estimate.',
  approval_edited:'Approval rejected: comments must be newly created and unedited.',
  approval_deleted:'Approval rejected: the comment was deleted before execution.',
  approval_unauthorized:'Approval rejected: only repository admins or named CODEOWNERS with write access can authorize spending.',
  approval_used:'Approval rejected: this comment already funded another run. A retry cannot create a new allowance.',
  invalid_approval_command:'Approval rejected: use the exact command shown with the quote.',
  invalid_approval_amount:'Approval rejected: use a positive USD amount with at most two decimal places.',
  pr_not_ready:'Review not performed: the PR must be open, ready for review, and target the default branch.',
};
const usd = n => '$'+(n/1e9).toFixed(2);
function render(reason, quote, budget, report) {
  const lines = [reasons[reason] || reasons.infrastructure_failed];
  if (budget) lines.push(`Committed inference so far: ${usd(Math.round(budget.committed_usd*1e9))}.`);
  if (Number.isInteger(report?.total_chunks)) lines.push(`Coverage: ${report.completed_chunks || 0}/${report.total_chunks} chunks. Full partial results remain in the report artifact.`);
  if (quote) {
    lines.push('',`Estimate: **${usd(quote.estimated_nanodollars)}**; proposed maximum new spend: **${usd(quote.cap_nanodollars)}**.`,
      `${quote.binding.model}, ${quote.binding.region}; ${quote.chunks} chunks, ${quote.cached_assessments} reusable assessments.`,
      `Pilot remaining: ${usd(Math.max(0,10000000000-quote.pilot_committed_nanodollars))}. Rates checked ${quote.binding.pricing.checked}.`,
      ...quote.items.map(i=>`${i.phase}: ${usd(i.estimated_nanodollars)} estimated / ${usd(i.reserved_nanodollars)} reserved (${i.calls} calls).`),
      '', 'Estimates use conservative token counts. The cap covers new inference for this revision, including qualification, verification, retries and summaries. Further subdivision may need another approval. Taxes and unrelated AWS charges are excluded.',
      `Expires: ${new Date(quote.expires_at*1000).toISOString()}.`,
      '', 'An admin or named CODEOWNER with write access can post:',
      '```',`/savior review approve ${quote.id} max-usd=${(quote.cap_nanodollars/1e9).toFixed(2)}`,'```');
  }
  return lines.join('\n');
}
module.exports = {render,reasons};
