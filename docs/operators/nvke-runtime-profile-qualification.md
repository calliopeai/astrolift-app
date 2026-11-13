# NVKE runtime profile qualification, 2026-11-13

The SDK, encrypted install-plan intent and UI parser accept the exact
`containerd/v2.3.4` profile alongside the five existing qualified families.
Neighboring patches and vendor-suffixed versions remain refused. This selection
does not establish live Node/runtime correspondence, image availability,
credentials, fresh phase use or permission to continue a failed installation.

The site screen presents its four fixed interface configurations as configuration
cards. Administrators retain all four configuration actions and the existing
reviewed dialog; other users cannot configure them. Operation history remains
the screen's single paginated table.

Completed local checks for this change:

- 37 provider SDK cases, zero failures/errors/skips.
- 14 real-PostgreSQL API cases, including encrypted intent and explicit recovery
  for both 2.3.0 and 2.3.4, zero failures/errors/skips. These use controlled SDK
  replies and do not qualify a native daemon.
- All 38 frontend runtime-profile cases, 24 NVKE interaction cases and five
  single-list guard cases pass. Existing NVKE Storybook cases pass.
- Changed-source Ruff, Prettier and ESLint checks pass. Canonical frontend lint,
  type checking and the production build completed successfully.

The exact frontend inputs now pass the full isolated Linux suite: 19,857 tests,
zero failures or pending tests, plus canonical lint and typecheck. The initial
production build in that run was killed with exit137; the kernel records a
container memory-limit OOM. A build-only run with the same 8,470 tracked App
inputs, installed dependencies and canonical `npm run build` command passes in
464.23 seconds. It retains two CPU affinity and a 4 GB V8 heap, with the container
limit increased from 6 GB to 10 GB. All tracked inputs remain byte-identical.
No test deadline, expectation or TypeScript check was relaxed.

Earlier macOS results remain recorded: 60 failures among 19,856 tests; after the
NVKE screen correction, 47 failures among the 7,167 selected tests. Linux passed
all 7,167 selected tests before the complete run above. The first selected Linux
run's typecheck exhausted Node's default 2 GB heap. These failed runs are retained
as resource/platform evidence; the later successful results do not relabel them.

The final current Opscode pin, combined native SDK qualification, full clean
installation/access/check, safe linked continuation and release/deployment gates
remain open. The existing exact SDK fixture pin is unchanged by this commit.

The isolated API and production Temporal worker have also adopted this App
revision. All 20 NVKE model-table snapshots and 10 operations remain unchanged,
with no active workflows. The worker registers 80 workflows and 220 activities;
the current SDK accepts 2.3.4 and refuses neighboring 2.3.5. Actual scoped CLI
reads preserve the original failed management operation at UNCERTAIN/version14.
Current-source CLI/API/mTLS agent verification, 34-workload review and a
controller-only remaining-install proposal pass. Private capability adoption,
reviewed execution and platform deployment remain open.
