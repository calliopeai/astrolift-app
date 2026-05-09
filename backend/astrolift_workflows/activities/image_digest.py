"""
Image digest pinning (#26 part, spec 12 §9).

After first push, every image reference the platform stores is in
``<repo>@sha256:<digest>`` form. Rollbacks promote a saved digest
so the bytes deployed are byte-identical to what shipped before.

Pure-Python parser + canonicalizer. The activity that resolves a
manifest's ``image = "..."`` calls ``pin_to_digest(ref, digest)``
to produce the platform-canonical form; the rollback workflow
calls ``parse_pinned_ref`` to recover the digest from a stored
DeploymentSnapshot.
"""

from __future__ import annotations

import dataclasses
import re

# Strict digest pattern: sha256: prefix + 64 lowercase hex chars.
# Mirrors OCI distribution spec — anything else is junk we won't
# trust to deploy.
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class ImageRefError(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class PinnedRef:
    """``<repo>@sha256:<digest>`` after pinning."""

    repo: str
    digest: str
    """``sha256:<64-hex>`` form."""

    def __str__(self) -> str:
        return f"{self.repo}@{self.digest}"


def is_digest(value: str) -> bool:
    """Validate a bare ``sha256:<hex>`` string."""
    return bool(DIGEST_RE.match(value))


def _split_at_digest(ref: str) -> tuple[str, str | None]:
    """Split a ref at ``@``. OCI refs use ``@`` as the digest
    separator and never as part of the repo or tag, so a single
    rsplit is unambiguous. Returns ``(left, digest_or_none)``."""
    if "@" not in ref:
        return ref, None
    left, _, right = ref.rpartition("@")
    return left, right


def _strip_tag(ref: str) -> str:
    """Strip a trailing ``:tag`` if present, leaving ``registry/repo``.
    Must NOT strip a ``:port`` from a registry hostname — the port
    is part of the repo. Heuristic: a tag has no slashes after it,
    while a port-followed-by-path does.

    ``registry:5000/foo/bar:v1`` → ``registry:5000/foo/bar``
    ``registry:5000/foo/bar``    → ``registry:5000/foo/bar``
    ``registry/foo/bar:v1``       → ``registry/foo/bar``
    """
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        # The colon is after the last slash → it's a tag separator.
        return ref[:last_colon]
    return ref


def parse_pinned_ref(value: str) -> PinnedRef:
    """Parse ``<repo>@sha256:<digest>``. Raises on anything else.

    Repo may include a registry hostname with port
    (``registry:5000/foo/bar``) — colons before the last slash
    are part of the repo, not a tag.
    """
    left, digest = _split_at_digest(value.strip())
    if digest is None:
        raise ImageRefError(
            f"image ref {value!r} is not in '<repo>@sha256:<digest>' form"
        )
    if not is_digest(digest):
        raise ImageRefError(
            f"image ref {value!r} carries non-sha256 digest {digest!r}"
        )
    if not left:
        raise ImageRefError(
            f"image ref {value!r} has empty repo before '@'"
        )
    return PinnedRef(repo=left, digest=digest)


def pin_to_digest(*, ref: str, digest: str) -> PinnedRef:
    """Combine a tagged or bare ref with a digest. Strips any tag —
    the digest is the canonical identity once we know it.

    ``ref`` may be ``registry/foo/bar``, ``registry/foo/bar:v1.2``,
    ``registry:5000/foo/bar:v1.2``, or already pinned
    ``registry/foo/bar@sha256:...`` (in which case the digest must
    match).
    """
    if not is_digest(digest):
        raise ImageRefError(
            f"digest {digest!r} not in 'sha256:<64-hex>' form"
        )

    ref = ref.strip()
    if not ref:
        raise ImageRefError("ref is empty")

    # Already pinned? Check digest matches.
    left, existing_digest = _split_at_digest(ref)
    if existing_digest is not None:
        if not is_digest(existing_digest):
            raise ImageRefError(
                f"image ref {ref!r} carries non-sha256 digest "
                f"{existing_digest!r}"
            )
        if existing_digest != digest:
            raise ImageRefError(
                f"image ref {ref!r} is already pinned to "
                f"{existing_digest!r}; refusing to repin to {digest!r}"
            )
        return PinnedRef(repo=left, digest=digest)

    # Tagged or bare ref — strip tag if present.
    repo = _strip_tag(left)
    return PinnedRef(repo=repo, digest=digest)


def is_pinned(value: str) -> bool:
    """Quick check the ref carries a digest."""
    if not value or "@" not in value:
        return False
    _, digest = _split_at_digest(value.strip())
    return digest is not None and is_digest(digest)
