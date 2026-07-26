"""StepUpMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import (
    MutationResultType,
)
from astrolift_graphql import (
    failure as gql_failure,
)
from astrolift_graphql import (
    success as gql_success,
)
from astrolift_identity.schema.mutations.types import (
    AssertSessionInput,
    AttestSessionInput,
    ElevateAdminSessionInput,
    RequestAttestationChallengeInput,
    _AttestationChallengePayload,
    _AttestationResult,
    _DeelevatePayload,
    _ElevatePayload,
)
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.tenancy import get_current_tenant


@strawberry.type
class StepUpMutations:
    # ---- Step-up auth (#487, spec 27 §4.1) ---------------------------
    #
    # ``elevateAdminSession`` flips a server-side timer on the current
    # session so sensitive mutations (secret writes, role grants,
    # force-redeploys, app deregistration) can run. The decorator
    # ``@requires_elevation`` on those mutations re-checks the timer
    # on every call; lapsed → deny envelope → FE re-prompts.
    #
    # Self-only — every authed user can elevate their own session;
    # no permission gate. The credential verifier (default: password
    # against the Django User row, swappable via
    # ``register_credential_verifier``) is what gates access. A
    # session with no underlying user (token-auth path) cannot
    # elevate and gets the same VALIDATION envelope as wrong creds.

    @strawberry.field
    @mutation_audit(action="auth.elevate_admin")
    def elevate_admin_session(
        self, info: Info, input: ElevateAdminSessionInput
    ) -> MutationResultType[_ElevatePayload]:
        from astrolift_identity.session_elevation import (
            KNOWN_METHODS,
            elevate,
            verify_credential,
        )

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        session = getattr(request, "session", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        if session is None:
            # Token-authed callers (API tokens) don't carry a session
            # bag; step-up is browser-session only by design. They
            # surface a typed error so the FE can hide the modal for
            # those callers.
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "step-up auth requires a browser session",
            )

        method = (input.method or "").strip().lower()
        if method not in KNOWN_METHODS:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown method {input.method!r}",
                field="method",
            )
        if not input.credential:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "credential is required",
                field="credential",
            )
        if not verify_credential(viewer, method, input.credential):
            # Audit the failed attempt so security review can spot
            # brute-force probes; bare-bones AuditEntry beats relying
            # on @mutation_audit's success/failure split because we
            # want a distinct ``auth.elevate_admin.failed`` row.
            tenant = get_current_tenant()
            try:
                emit_audit(
                    AuditEntry(
                        actor_user_id=tenant.actor_user_id if tenant else viewer.pk,
                        organization_id=tenant.organization_id if tenant else None,
                        action="auth.elevate_admin.failed",
                        decision="DENY",
                        target_kind="user",
                        target_id=viewer.pk,
                        duration_ms=0,
                        permissions=(),
                        error_code=ErrorCode.PERMISSION_DENIED.value,
                        error_message="invalid credential for step-up",
                        extra={"method": method},
                    )
                )
            except Exception:  # noqa: BLE001
                import logging as _log

                _log.getLogger(__name__).exception("elevate_admin_session: failed-attempt audit emit raised")
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "invalid credential",
                field="credential",
            )

        status = elevate(session, method=method, ttl_seconds=input.ttl_seconds)
        return gql_success(
            _ElevatePayload(
                elevated_until=status.elevated_until,  # type: ignore[arg-type]
                seconds_remaining=status.seconds_remaining,
                method=method,
            )
        )

    @strawberry.field
    @mutation_audit(action="auth.deelevate_admin")
    def deelevate_admin_session(self, info: Info) -> MutationResultType[_DeelevatePayload]:
        from astrolift_identity.session_elevation import deelevate, get_status

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        session = getattr(request, "session", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")
        if session is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "step-up auth requires a browser session",
            )

        was_elevated = get_status(session).elevated
        deelevate(session)
        return gql_success(_DeelevatePayload(previously_elevated=was_elevated))

    # ---- Device attestation (#496) ---------------------------------
    #
    # ``requestAttestationChallenge`` issues a server-side nonce the
    # mobile SDK incorporates into its App Attest / Play Integrity
    # blob. ``attestSession`` consumes the nonce + verifies the blob
    # against Apple / Google and persists the outcome on the session
    # sidecar. ``assertSession`` (iOS only) periodically re-checks the
    # attested key + bumps the monotonic counter so a replayed
    # assertion is rejected.
    #
    # All three are self-only — every authed user attests their own
    # sessions; no permission gate. The verifier rejects on its own
    # if the install hasn't configured IOS_APP_ID / ANDROID_PACKAGE_NAME.

    @strawberry.field
    @mutation_audit(action="auth.attestation.challenge_issued")
    def request_attestation_challenge(
        self, info: Info, input: RequestAttestationChallengeInput
    ) -> MutationResultType[_AttestationChallengePayload]:
        from astrolift_identity.attestation import AttestationError
        from astrolift_identity.attestation.service import issue_challenge

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        try:
            row = issue_challenge(user=viewer, kind=input.kind)
        except AttestationError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, exc.message, field="kind")
        return gql_success(
            _AttestationChallengePayload(
                challenge=row.nonce,
                expires_at=row.expires_at,
                kind=row.kind,
            )
        )

    @strawberry.field
    @mutation_audit(action="auth.attestation.submitted")
    def attest_session(self, info: Info, input: AttestSessionInput) -> MutationResultType[_AttestationResult]:
        from astrolift_identity.attestation import AttestationError
        from astrolift_identity.attestation.service import attest_session
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        # Bind to the current session sidecar. record_session() find-
        # or-creates the row if the middleware hasn't yet — happens on
        # the first request of a newly issued session.
        row = record_session(request)
        if row is None:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key:
                row = AstroliftSession.objects.filter(session_key=current_key, user=viewer).first()
        if row is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "no active session to attest",
            )

        try:
            attest_session(
                session=row,
                kind=input.kind,
                challenge=input.challenge,
                attestation_object=input.attestation_object,
                key_id=input.key_id,
                integrity_token=input.integrity_token,
            )
        except AttestationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{exc.reason_code}: {exc.message}",
                field="attestationObject",
            )

        row.refresh_from_db()
        return gql_success(
            _AttestationResult(
                trust_level=row.attestation_trust_level,
                kind=row.attestation_kind,
                attested_at=row.attestation_verified_at,
                reason=None,
            )
        )

    @strawberry.field
    @mutation_audit(action="auth.attestation.asserted")
    def assert_session(self, info: Info, input: AssertSessionInput) -> MutationResultType[_AttestationResult]:
        from astrolift_identity.attestation import AttestationError
        from astrolift_identity.attestation.service import assert_session
        from astrolift_identity.models import AstroliftSession
        from astrolift_identity.sessions import record_session

        request = getattr(info.context, "request", None)
        viewer = getattr(request, "user", None) if request else None
        if viewer is None or not viewer.is_authenticated:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "not authenticated")

        row = record_session(request)
        if row is None:
            current_key = getattr(getattr(request, "session", None), "session_key", None)
            if current_key:
                row = AstroliftSession.objects.filter(session_key=current_key, user=viewer).first()
        if row is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "no active session to assert",
            )

        try:
            assert_session(
                session=row,
                challenge=input.challenge,
                assertion=input.assertion,
            )
        except AttestationError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"{exc.reason_code}: {exc.message}",
                field="assertion",
            )

        row.refresh_from_db()
        return gql_success(
            _AttestationResult(
                trust_level=row.attestation_trust_level,
                kind=row.attestation_kind,
                attested_at=row.attestation_verified_at,
                reason=None,
            )
        )
