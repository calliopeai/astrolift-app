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

Release verification remains incomplete. The whole frontend run reported
60 failures among 19,856 tests. After the NVKE screen correction, a serial run of
the 21 selected suites reported 7,120 passes and 47 failures among 7,167 tests,
with no pending tests. Remaining failures are outside NVKE; their cause has not
been established. A separate isolated Linux run uses the same assertions and
deadlines. No timeout or expectation was relaxed and neither run is called green.

The final current Opscode pin, combined native SDK qualification, full clean
installation/access/check, safe linked continuation and release/deployment gates
remain open. The existing exact SDK fixture pin is unchanged by this commit.
