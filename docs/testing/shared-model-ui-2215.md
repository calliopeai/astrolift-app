# Shared model UI — #2215

The subscription view is a pure Storybook surface, pending the #2213 typed
GraphQL adapter and production route integration. Its props are presentation
facts, not a new server API or a source of permission authority.

New subscriptions require a named lowercase alias matching
`[a-z][a-z0-9_]{0,31}`. Existing legacy `MODEL_*` bindings remain readable.
The target picker and subscription list use embedded `ListPage` surfaces with
server search and pagination supplied by their adapters. A reviewed target must
still be on the verified current page. Changing pages requires a fresh review;
no browser filtering or cap hides later eligible environments.

The review captures organization, model and environment identities and versions;
revocation captures the attachment identity/version as well. Identity changes
clear the form/review state. Target, model or attachment replacement permanently
invalidates an old review, including changing away and back. Late accepted or
failed replies cannot supply completion for a replaced context. The backend must
still enforce its live ownership and version preconditions.

Adding or revoking a subscription requests a shared model restart. The view
warns that all consumers may temporarily lose access and distinguishes accepted
requests from applied access. Only server-provided reconciliation status can show
active or revoked. Unknown/unsupported credential-list runtimes block review.
Credentials are references on the server; the view has no credential-value props.

All eight locales supply translated presentation text. Immutable aliases, IDs,
binding prefixes and server diagnostics remain literal. The view's existing
subscription list may include legacy aliases; the new-subscription form cannot
create one.

Validation for the view layer includes React interactions and portable stories:
exact request identities, required aliases, explicit refusal/retry, model and
target version changes, organization A→B→A, late subscribe/revoke replies,
unverified runtime admission, server search/paging callbacks and rendered locale
copy. This is not yet proof of #2215 production HF/cluster/subscription journeys,
shared playground dispatch or metrics availability. Those acceptance items remain
open until their actual API adapters and browser checks land.
