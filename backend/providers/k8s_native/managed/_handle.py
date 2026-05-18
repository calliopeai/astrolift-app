"""Handle encoding for in-cluster managed services.

A managed-service handle is a string that uniquely identifies a
provisioned resource so the workflow layer can hand it back to the
driver for ``status``, ``binding``, ``update``, and ``deprovision``.
For in-cluster (k8s_native) drivers a handle must carry enough
locator metadata for ``deprovision`` to issue
``cluster_driver.delete_manifests(cluster_id, namespace, ...)``
without re-deriving it from a long-lost ``ProvisionSpec``.

The legacy format was ``"<kind>/<name>"``. That's lossy: the
provisioner knows the tenant_cluster_id + namespace at apply time,
but the workflow only carries the returned handle string. So
``deprovision`` was either a no-op or guessed the locator and
delivered the delete request to the wrong namespace.

The new format encodes both:

    "<kind>/<cluster_id>/<namespace>/<name>"

``kind`` and ``name`` keep their pre-existing positions (segment 0
and segment -1) so ``binding()`` paths that only need the resource
name continue to work for both shapes; ``unpack`` reads the
4-segment form and falls back to the 2-segment legacy shape with
empty locator fields so callers can distinguish "old handle" from
"new handle".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedHandle:
    """Decoded locator from a managed-service handle.

    ``cluster_id`` and ``namespace`` are empty strings when the
    handle is a legacy 2-segment value — callers that need them for
    a delete should return an error rather than guess.
    """

    kind: str
    cluster_id: str
    namespace: str
    name: str

    @property
    def is_legacy(self) -> bool:
        return self.cluster_id == "" and self.namespace == ""


def pack(*, kind: str, cluster_id: str, namespace: str, name: str) -> str:
    """Encode a 4-segment handle.

    None of the segments may contain ``/`` — the encoder doesn't
    escape, and the upstream callers control all four values so
    there's no foreign input to defend against. Empty
    cluster_id/namespace are rejected because the whole point of
    this format is to carry them.
    """
    if not cluster_id or not namespace:
        msg = (
            "managed-service handle requires non-empty cluster_id "
            "and namespace; got "
            f"cluster_id={cluster_id!r} namespace={namespace!r}"
        )
        raise ValueError(msg)
    for segment in (kind, cluster_id, namespace, name):
        if "/" in segment:
            msg = f"handle segment may not contain '/': {segment!r}"
            raise ValueError(msg)
    return f"{kind}/{cluster_id}/{namespace}/{name}"


def unpack(handle: str) -> ParsedHandle:
    """Decode a handle into its parts.

    Supports both the legacy 2-segment ``"<kind>/<name>"`` form (the
    locator fields come back empty) and the 4-segment form. Anything
    with 3 segments or 5+ segments is an error — silently coercing
    would mask a bug in the producer.
    """
    parts = handle.split("/")
    if len(parts) == 2:
        kind, name = parts
        return ParsedHandle(
            kind=kind,
            cluster_id="",
            namespace="",
            name=name,
        )
    if len(parts) == 4:
        kind, cluster_id, namespace, name = parts
        return ParsedHandle(
            kind=kind,
            cluster_id=cluster_id,
            namespace=namespace,
            name=name,
        )
    msg = (
        "managed-service handle must be 2-segment "
        "(<kind>/<name>) or 4-segment "
        f"(<kind>/<cluster>/<ns>/<name>); got {handle!r}"
    )
    raise ValueError(msg)
