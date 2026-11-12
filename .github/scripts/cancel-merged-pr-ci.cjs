const active = new Set(['queued', 'in_progress', 'waiting', 'pending', 'requested']);

module.exports = async function cancelMergedPrCi({ github, context, core }) {
  const pull = context.payload.pull_request;
  const { owner, repo } = context.repo;
  if (context.eventName !== 'pull_request_target' || !pull?.merged ||
      pull.base.ref !== 'main' || pull.base.repo.full_name !== `${owner}/${repo}`) {
    return;
  }

  const belongsToPull = (run) => {
    if (run.event !== 'pull_request' || !pull.head.repo ||
        run.head_repository?.id !== pull.head.repo.id) return false;
    if (run.pull_requests?.length) {
      return run.pull_requests.some((p) => p.number === pull.number &&
        p.base.repo.id === pull.base.repo.id);
    }
    // GitHub can remove PR associations from runs after merging. In that case
    // require both the final PR head SHA and branch in the same source repo.
    return run.head_sha === pull.head.sha && run.head_branch === pull.head.ref;
  };

  // Collect every page before cancelling: status changes must not shift pages.
  const runs = await github.paginate(github.rest.actions.listWorkflowRuns, {
    owner, repo, workflow_id: 'ci.yml', event: 'pull_request',
    branch: pull.head.ref, per_page: 100,
  });
  for (const run of runs) {
    if (!belongsToPull(run) || !active.has(run.status)) continue;
    const args = { owner, repo, run_id: run.id };
    const { data: current } = await github.rest.actions.getWorkflowRun(args);
    if (!belongsToPull(current) || current.workflow_id !== run.workflow_id ||
        !active.has(current.status)) continue;
    try {
      await github.rest.actions.cancelWorkflowRun(args);
      core.info(`Requested cancellation of PR #${pull.number} CI run ${run.id}.`);
    } catch (error) {
      // A run can finish between the status read and the cancellation request.
      if (error.status !== 409) throw error;
      const { data: after } = await github.rest.actions.getWorkflowRun(args);
      if (after.status !== 'completed') throw error;
      core.info(`PR CI run ${run.id} completed before cancellation.`);
    }
  }
};
