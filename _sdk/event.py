"""EventDriver protocol (#57) -- forward Kubernetes / cluster events.

Variants: webhook (generic HTTP POST), eventarc (GCP), eventbridge
(AWS), event_grid (Azure), kafka (in-cluster), nats, slack, pagerduty.

Events feed alerting + audit trails. The driver normalizes
Kubernetes event objects + custom application events into a common
EventEnvelope shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class EventEnvelope:
    timestamp: str
    severity: str
    """info | warning | error | critical"""

    source: str
    """Originating component, e.g. 'kube-event' | 'app-runtime' |
    'platform-workflow'."""

    namespace: str
    cluster: str
    title: str
    body: str
    labels: dict[str, str] = field(default_factory=dict)
    related: list[str] = field(default_factory=list)
    """References to related resources, e.g. Pod/api-0,
    Deployment/api."""


@dataclass(frozen=True)
class ForwardResult:
    accepted: int
    rejected: int
    errors: list[str] = field(default_factory=list)


class EventDriver(Protocol):
    """Protocol for forwarding cluster + application events."""

    def forward(self, events: list[EventEnvelope]) -> ForwardResult: ...

    def health_check(self) -> bool: ...
