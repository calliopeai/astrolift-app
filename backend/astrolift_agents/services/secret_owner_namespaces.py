"""Owner namespace checks for explicit maintenance tools; runtime cutover is separate."""

from astrolift_dispatch.agent_secrets import (
    _SECRET_SEGMENT_RE,
    _SECRET_STORE_SCHEMES,
    SecretRefNamespaceError,
    canonical_secret_ref,
)


def agent_secret_prefix(spec) -> str:
    """The typed-secret namespace of a live spec owner (#2102).

    A stale owner never becomes shared. Projects take precedence over teams,
    matching spec authorization, but inconsistent ancestry is also refused.
    """
    organization = spec.organization
    if getattr(spec, "deleted_at", None) is not None or getattr(organization, "deleted_at", None) is not None:
        raise SecretRefNamespaceError("agent secret namespace requires a live organization")
    prefix = f"agents/{organization.guid}/"
    if getattr(spec, "project_id", None) is not None:
        project = spec.project
        team = project.team
        if (
            project.organization_id != organization.pk
            or project.deleted_at is not None
            or team.organization_id != organization.pk
            or team.deleted_at is not None
            or (spec.team_id is not None and spec.team_id != project.team_id)
        ):
            raise SecretRefNamespaceError("agent secret namespace requires a live, consistent project owner")
        return f"{prefix}projects/{project.guid}/"
    if getattr(spec, "team_id", None) is not None:
        team = spec.team
        if team.organization_id != organization.pk or team.deleted_at is not None:
            raise SecretRefNamespaceError("agent secret namespace requires a live team owner")
        return f"{prefix}teams/{team.guid}/"
    return f"{prefix}shared/"


def _spec_secret_ref_reason(uri: str, *, spec) -> str | None:
    try:
        prefix = agent_secret_prefix(spec)
    except SecretRefNamespaceError as exc:
        return str(exc)
    path, separator, field = uri.partition("#")
    for scheme in _SECRET_STORE_SCHEMES:
        if path.startswith(scheme):
            path = path[len(scheme) :]
            break
    path = path.lstrip("/").removeprefix("astrolift/")
    suffix = path.removeprefix(prefix)
    if (
        path.startswith(prefix)
        and suffix
        and all(_SECRET_SEGMENT_RE.fullmatch(s) and s not in (".", "..") for s in suffix.split("/"))
        and (not separator or bool(_SECRET_SEGMENT_RE.fullmatch(field)))
    ):
        return None
    return f"agent secret is outside the spec owner's secret namespace; it must live under {prefix}"


def assert_spec_scoped_secret_ref(uri: str, *, spec) -> None:
    """Validate the same canonical owner path dispatch and value CRUD read."""
    reason = _spec_secret_ref_reason(canonical_secret_ref(uri), spec=spec)
    if reason is not None:
        raise SecretRefNamespaceError(reason)
