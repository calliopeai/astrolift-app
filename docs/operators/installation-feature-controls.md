# Installation feature controls

The administration Features screen changes only the existing server-allowlisted
runtime flags. Server authorization remains authoritative; this recovery flow
does not add permissions or editable flags. Build-time features remain read-only.

A successful `SetFeatureFlag` reply confirms acceptance only when its returned
key and enabled value match the requested change and the reply has no errors.
The screen closes confirmation and retains that accepted value even if the
subsequent inventory, navigation or viewer read fails. Malformed runtime or
build-time entries and failed navigation/viewer cache updates also keep write
controls read-only. Use **Refresh current state** to retry those reads; it does
not repeat the mutation.

A missing, lost or inconsistent write reply is uncertain rather than a confirmed
rejection. A fresh network inventory containing the affected flag, together with
successful navigation and unchanged-viewer reads and cache updates, resolves
recovery to the currently observed value. That value may differ from the request;
the refresh cannot prove whether the earlier write was applied and later changed.
Only an explicit server rejection is reported as a refused write and permits the
existing confirmation to remain actionable without an automatic refresh.

Concurrent clicks cannot send another write while one is pending. Responses from
an earlier viewer or Apollo client context cannot update the new context or its
navigation caches. A changed or unavailable viewer requires a new current read
before writes resume. These are browser recovery protections, not server-side
idempotency or immutable-target guarantees. Local tests do not establish release
or live installation acceptance for #2281.
