# Changelog

## Unreleased

- Persisted deployment logs retain actual owner deny policies and current bearer
  ceilings after app/environment teardown or cluster retirement. Failed history
  diagnostics preserve the original provider failure; completion writes still
  fail the activity when durable storage is unavailable. Durable database defaults
  keep old log writers compatible during migration-first rollout (#2176).

- Production route checks follow rendered navigation for every active route in
  the generated dictionary (#2171), with separate alias, parked-route and role
  checks. Cold workflow-run pages wait for workflow context before rendering.
  See [route navigation checks](docs/testing/route-navigation.md).
