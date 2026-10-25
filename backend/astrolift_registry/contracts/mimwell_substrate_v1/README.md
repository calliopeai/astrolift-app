# Published Mimwell substrate contract

This directory vendors the unmodified data contract from the published public
Mimwell `v0.3.6` prerelease, commit
`e59de019b3d10564fdc1301caef06842ab84256d`:
[substrate-cells.json](https://github.com/ConflictHQ/mimwell/blob/e59de019b3d10564fdc1301caef06842ab84256d/substrate-cells.json).

- Schema version: 1; all 192 Cartesian cells, with their original status/reason.
- Exact bytes: 43,198.
- SHA-256: `0c30ad508da54934cb42787f137c28d5e4aebbbb9c3199ae32ac29e560acfcb5`.
- Copyright 2026 CONFLICT LLC; Apache-2.0. Original `LICENSE` and `NOTICE` are
  copied unmodified beside the data.

Astrolift consumes the table and fails closed on a missing, altered, oversized,
malformed, duplicate or incomplete contract. It does not import/run private
Mimwell code or reproduce its status algorithm. The process caches only this
verified immutable deployment artifact; requests cannot select or refresh it.
A future data update requires an explicit reviewed source/pin change and normal
qualification. This pin is not unpublished public 0.3.7/S4 or private M76.

A supported matrix cell describes engine placement, not installed authority,
protection, a bucket/credential binding, volume attachment, source authorization,
App/Metric permission, workload identity or completed graduation. Those are
independently verified host/brain operations. The existing manifest renderer
retains its separately documented historical generator provenance.

The legacy substrate-update path refuses an explicit `brain.authority` or a
reserved root `contract.json` before changing repository or platform state.
It cannot safely rewrite native private storage into its historical `/data`
projection layout or choose native replicas. A contract declaration is not
proof of adoption. Ordinary configs without those native declarations keep
their compiled-projection layout and existing replica behavior. An owner-
reviewed native manifest remains separate from this selector; this refusal
does not implement native provisioning or graduation.
