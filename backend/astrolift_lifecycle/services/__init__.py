"""Lifecycle services — synchronous, non-Temporal helpers that wrap
cluster-driver calls used by GraphQL mutations.

Temporal activities live under ``astrolift_workflows.activities``; this
package is for the first-line ops actions a GraphQL mutation invokes
directly (rolling restart, replica scale, etc.) — they're idempotent
single-shot calls that don't need workflow orchestration.
"""
