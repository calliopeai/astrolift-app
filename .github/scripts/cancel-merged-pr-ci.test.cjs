const { test } = require('node:test');
const assert = require('node:assert/strict');
const cancel = require('./cancel-merged-pr-ci.cjs');

function fixture() {
  const context = {
    eventName: 'pull_request_target', repo: { owner: 'org', repo: 'app' },
    payload: { pull_request: {
      number: 42, merged: true,
      base: { ref: 'main', repo: { id: 1, full_name: 'org/app' } },
      head: { ref: 'fix/example', sha: 'final-head', repo: { id: 2 } },
    } },
  };
  const run = (id, extra = {}) => ({
    id, workflow_id: 7, status: 'in_progress', event: 'pull_request',
    head_repository: { id: 2 }, head_sha: 'final-head', head_branch: 'fix/example',
    pull_requests: [{ number: 42, base: { repo: { id: 1 } } }], ...extra,
  });
  const runs = [];
  const cancelled = [];
  const reads = [];
  const github = {
    paginate: async (_method, args) => {
      assert.equal(args.workflow_id, 'ci.yml');
      assert.equal(args.event, 'pull_request');
      assert.equal(args.branch, 'fix/example');
      assert.equal(args.per_page, 100);
      return runs;
    },
    rest: { actions: {
      listWorkflowRuns: () => {},
      getWorkflowRun: async ({ run_id }) => {
        reads.push(run_id);
        return { data: runs.find((r) => r.id === run_id) };
      },
      cancelWorkflowRun: async ({ run_id }) => cancelled.push(run_id),
    } },
  };
  return { context, github, core: { info() {} }, run, runs, cancelled, reads };
}

test('cancels only this PR active CI; keeps main, other PRs and finished runs', async () => {
  const f = fixture();
  f.runs.push(f.run(1), f.run(2, { status: 'queued' }),
    f.run(3, { event: 'push' }), f.run(4, { status: 'completed' }),
    f.run(5, { pull_requests: [{ number: 43, base: { repo: { id: 1 } } }] }),
    f.run(6, { head_repository: { id: 3 } }),
    f.run(7, { pull_requests: [], head_sha: 'different-head' }),
    f.run(8, { event: 'workflow_dispatch' }),
    f.run(9, { pull_requests: [{ number: 42, base: { repo: { id: 99 } } }] }));
  await cancel(f);
  assert.deepEqual(f.cancelled, [1, 2]);
  assert.deepEqual(f.reads, [1, 2]);
});

test('matches merged runs with empty PR associations only by exact source identity', async () => {
  const f = fixture();
  f.runs.push(f.run(1, { pull_requests: [] }),
    f.run(2, { pull_requests: [], head_branch: 'other-branch' }),
    f.run(3, { pull_requests: [], head_sha: 'older-head' }),
    f.run(4, { pull_requests: [], head_repository: { id: 99 } }));
  await cancel(f);
  assert.deepEqual(f.cancelled, [1]);
});

test('unmerged closures, other bases and untrusted events do nothing', async () => {
  for (const change of [
    (f) => { f.context.payload.pull_request.merged = false; },
    (f) => { f.context.payload.pull_request.base.ref = 'release'; },
    (f) => { f.context.payload.pull_request.base.repo.full_name = 'other/app'; },
    (f) => { f.context.eventName = 'pull_request'; },
  ]) {
    const f = fixture(); change(f);
    f.github.paginate = async () => assert.fail('must not enumerate runs');
    await cancel(f);
  }
});

test('rechecks status and identity before cancellation', async () => {
  for (const changed of [{ status: 'completed' }, { event: 'push' }, { workflow_id: 9 }]) {
    const f = fixture(); f.runs.push(f.run(1));
    f.github.rest.actions.getWorkflowRun = async () => ({ data: f.run(1, changed) });
    await cancel(f);
    assert.deepEqual(f.cancelled, []);
  }
});

test('tolerates completion race but surfaces permission and active-run failures', async () => {
  for (const [status, after, rejects] of [[409, 'completed', false],
    [409, 'in_progress', true], [403, 'completed', true]]) {
    const f = fixture(); f.runs.push(f.run(1));
    let reads = 0;
    f.github.rest.actions.getWorkflowRun = async () => ({
      data: f.run(1, { status: ++reads === 1 ? 'in_progress' : after }),
    });
    f.github.rest.actions.cancelWorkflowRun = async () => {
      throw Object.assign(new Error('cancel rejected'), { status });
    };
    if (rejects) await assert.rejects(cancel(f), /cancel rejected/);
    else await cancel(f);
  }
});
