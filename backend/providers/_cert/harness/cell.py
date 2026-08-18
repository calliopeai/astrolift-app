"""One manifest cell, reduced to what the cycle needs to run it.

The manifests under ``verification/manifests`` are the campaign's input, and
they carry more than the runner uses -- resource requests, container commands,
per-cell prose. This reads out the handful of things the seven steps assert
against, and refuses a manifest that cannot be run rather than running a
degraded version of it.

Two refusals are deliberate.

*A cell with no public workload* has no ingress to reach and no ``/selftest`` to
call, so VERIFY-UP would pass by having nothing to check. Every manifest in the
collection has one; the refusal exists so that stays true.

*A negative cell* is not an unattended cycle. Each one encodes a precondition
that has to be created out of band -- stripping an identity tag off a live
resource, racing a teardown against a provision -- and its expected outcome is a
refusal, not a green cycle. Running one through this runner would report the
refusal it is testing for as a BUILDOUT failure. They are run by hand against
the assertions written into each manifest.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class UnrunnableCell(ValueError):
    """The manifest cannot be driven through an unattended cycle."""


@dataclass(frozen=True)
class Binding:
    kind: str
    name: str
    variant: str


@dataclass(frozen=True)
class Cell:
    """A manifest cell the runner can execute."""

    identifier: str
    """The campaign's own cell id, e.g. ``happy-path/aws`` -- the label every
    report row is keyed by."""

    cloud: str
    app_name: str
    image_ref: str
    public_workload: str
    health_path: str
    workloads: tuple[str, ...]
    bindings: tuple[Binding, ...]
    environment: str = "production"
    path: Path | None = None

    @property
    def image_tag(self) -> str:
        """The tag half of the pinned image reference, which is what
        ``startDeployment`` takes."""
        _, _, tag = self.image_ref.rpartition(":")
        return tag

    @classmethod
    def load(cls, path: Path | str, *, environment: str = "production") -> Cell:
        path = Path(path)
        return cls.from_toml(tomllib.loads(path.read_text(encoding="utf-8")), path=path, environment=environment)

    @classmethod
    def from_toml(cls, data: dict, *, path: Path | None = None, environment: str = "production") -> Cell:
        where = str(path) if path else "<manifest>"
        campaign = data.get("campaign") or {}
        if "negative" in campaign:
            case = campaign["negative"].get("case", "unnamed")
            raise UnrunnableCell(
                f"{where}: negative cell {case!r} is not an unattended cycle. Its precondition has "
                f"to be created out of band and its expected outcome is a refusal, which this runner "
                f"would report as a BUILDOUT failure. Run it by hand against the assertions in the manifest."
            )

        workloads = data.get("workloads") or []
        if not workloads:
            raise UnrunnableCell(f"{where}: no workloads")
        public = [w for w in workloads if w.get("is_public")]
        if not public:
            raise UnrunnableCell(
                f"{where}: no public workload, so VERIFY-UP has no ingress to reach and no "
                f"/selftest to call -- it would pass by having nothing to check"
            )

        primary = _primary_container(public[0], where)
        health = (primary.get("healthcheck") or {}).get("value") or "/health"

        return cls(
            identifier=str(campaign.get("cell") or where),
            cloud=str(campaign.get("cloud", "")),
            app_name=str(data.get("name", "")),
            image_ref=str(primary.get("image_ref", "")),
            public_workload=str(public[0].get("name", "")),
            health_path=str(health),
            workloads=tuple(str(w.get("name", "")) for w in workloads),
            bindings=tuple(
                Binding(
                    kind=str(service.get("kind", "")),
                    name=str(service.get("name", "")),
                    variant=str(service.get("variant", "")),
                )
                for service in data.get("managed_services") or []
            ),
            environment=environment,
            path=path,
        )


def _primary_container(workload: dict, where: str) -> dict:
    containers = workload.get("containers") or []
    if not containers:
        raise UnrunnableCell(f"{where}: public workload {workload.get('name')!r} has no containers")
    return next((c for c in containers if c.get("is_primary")), containers[0])
