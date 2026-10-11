# Private native identity operation telemetry

Owned AWS role reconciliation and verification use `driver_op` for operation
counts, durations, spans and entry heartbeats. Reconciliation also emits the
existing best-effort audit event. All keyword arguments are redacted: role
names, grants, GUID ownership and service-account subjects are private metadata.
The Azure owned-reconciler factory records an operation without an effect audit
or heartbeat; creating a reconciler does not establish native readiness.

These operations opt into `redact_errors=True`. Their logs contain the exception
class without its message or traceback; spans retain a fixed error status without
exception events; audit errors contain the same fixed message. The original
exception object is rethrown unchanged so native retry/error classification
continues to work. The option defaults to false, preserving existing driver
telemetry behavior.

Argument redaction is explicit and covers keyword arguments only. This decorator
does not sanitize a driver's internal logs or surrounding caller telemetry.
Callers handling private native failures must apply their own privacy controls;
they must not log the original exception message or traceback. Instrumentation
does not grant authority, prove durable audit persistence, or change native
ownership, reconciliation, or readiness semantics.
