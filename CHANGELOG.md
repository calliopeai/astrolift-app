# Changelog

## Unreleased


- Bind managed GitHub workflow creation and updates to the reviewed absence or
  blob SHA. Refuse concurrent edits, creation and deletion without advancing
  sync receipts; preserve independent edits on reconciliation PR branches.
  Providers without conditional writes fail closed when that contract is
  requested (#2139).

- Translate connected agent model/session controls and tool-registry navigation,
  filters, states and feedback in all eight locales. Preserve API identifiers and
  bound pending control feedback to the selected environment specification (#2145).
