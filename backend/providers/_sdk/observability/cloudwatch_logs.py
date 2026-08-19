"""CloudWatch Logs LogQueryDriver (#1111).

The historical (time-range) log surface — ``LogQueryDriver`` — for AWS
clusters that ship pod logs to CloudWatch Logs (the SteadyMD default:
Fluent Bit / the CloudWatch agent write EKS pod logs to a Container
Insights ``application`` log group). Loki ships the streaming side; this
ships the paginated query side for CloudWatch.

Wired from ``core.cluster_log_query.resolve_log_query_driver`` when a
cluster's ``provider_config`` carries::

    {
      "log_driver": "cloudwatch_logs",
      "log_config": {
        "log_group": "/aws/containerinsights/<cluster>/application",
        "region": "us-west-2",
        "log_stream_name_prefix": "<optional stream prefix>"
      }
    }

Credentials follow the same ambient-IRSA pattern the other AWS drivers
use (``boto3.client("logs", region_name=...)`` — the control plane's
role carries ``logs:FilterLogEvents`` on the tenant log group); a
cross-account ``role_arn`` in the config is assumed via STS when set.
Never hardcodes credentials.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from _sdk.log_stream import LogLine, LogPage

log = logging.getLogger(__name__)

# Pull ``app="x"`` / ``namespace="x"`` / ``workload="x"`` back out of the
# LogQL-flavoured selector ``core.cluster_log_query.build_app_selector``
# emits, so the CloudWatch filter can scope to the app's pods within a
# shared Container Insights log group.
_SELECTOR_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class CloudWatchLogsConfig:
    log_group: str
    region: str
    log_stream_name_prefix: str | None = None
    role_arn: str | None = None
    """Optional cross-account role to assume for a tenant cluster in a
    different AWS account than the control plane. Ambient creds when
    unset."""

    client: Any | None = None
    """Injected boto3 ``logs`` client — tests pass a fake / moto client;
    production builds one from ``region`` (+ ``role_arn``)."""


class CloudWatchLogsQueryDriver:
    """``LogQueryDriver`` backed by CloudWatch Logs ``FilterLogEvents``.

    FilterLogEvents (rather than Logs Insights) keeps the query
    synchronous + cursor-paged, which maps cleanly onto the
    ``LogPage`` contract (Insights is start-query/poll/get-results —
    three round-trips and no native cursor)."""

    def __init__(self, *, config: CloudWatchLogsConfig) -> None:
        self._config = config
        if config.client is not None:
            self._logs = config.client
        else:
            self._logs = _build_logs_client(region=config.region, role_arn=config.role_arn)

    def query_logs(
        self,
        query: str,
        since: str,
        until: str,
        *,
        limit: int,
        cursor: str,
        level: str | None,
        search: str | None,
    ) -> LogPage:
        # ``level`` is accepted for protocol parity but not pushed down —
        # the resolver re-applies the level filter uniformly across all
        # aggregators, so pushing it here would be redundant.
        del level
        labels = _parse_selector(query)

        kwargs: dict[str, Any] = {
            "logGroupName": self._config.log_group,
            "startTime": _iso_to_ms(since),
            "endTime": _iso_to_ms(until),
            "limit": max(1, limit),
        }
        if self._config.log_stream_name_prefix:
            kwargs["logStreamNamePrefix"] = self._config.log_stream_name_prefix
        if cursor:
            kwargs["nextToken"] = cursor

        # Scope to the app's pods (their names carry the app slug) plus
        # any operator search term. Space-separated quoted terms are an
        # AND match in CloudWatch's filter-pattern grammar.
        pattern_terms = [t for t in (labels.get("app"), labels.get("workload"), search) if t]
        if pattern_terms:
            kwargs["filterPattern"] = " ".join(f'"{_escape_pattern(t)}"' for t in pattern_terms)

        try:
            response = self._logs.filter_log_events(**kwargs)
        except Exception:
            log.exception(
                "cloudwatch_logs: filter_log_events failed for group %s",
                self._config.log_group,
            )
            raise

        fallback_ns = labels.get("namespace", "")
        items = [_event_to_line(ev, fallback_namespace=fallback_ns) for ev in response.get("events", [])]
        return LogPage(
            items=items,
            next_cursor=response.get("nextToken", "") or "",
            # CloudWatch doesn't cheaply report whether the window
            # predates retention; the resolver treats False as "not
            # known to be truncated", same as Loki.
            reached_retention=False,
            total_count=-1,
        )


def _build_logs_client(*, region: str, role_arn: str | None) -> Any:
    """Build a boto3 CloudWatch Logs client using ambient credentials,
    assuming ``role_arn`` first for a cross-account tenant cluster.

    Delegates to ``aws.session`` (#1422) rather than assuming inline. This
    driver had the tree's only cross-account path, and hand-rolling it meant
    an AssumeRole per query with no expiry handling; the shared factory caches
    the session and is the seam the remaining AWS drivers migrate onto.
    Imported lazily for the same reason boto3 was: an ``_sdk`` consumer that
    never touches AWS must not need the AWS extra installed."""
    from _sdk.cloud_credentials import CloudCredential, CredentialMode
    from aws.session import aws_client

    if not role_arn:
        return aws_client("logs", region=region)

    credential = CloudCredential(
        cloud="aws",
        mode=CredentialMode.AWS_ASSUME_ROLE,
        role_arn=role_arn,
        session_name="astrolift-log-query",
    )
    return aws_client("logs", region=region, credential=credential)


def _parse_selector(query: str) -> dict[str, str]:
    """Extract label=value pairs from the LogQL-flavoured selector."""
    return {m.group(1): m.group(2) for m in _SELECTOR_LABEL.finditer(query or "")}


def _iso_to_ms(iso: str) -> int:
    """ISO-8601 string → epoch milliseconds (CloudWatch's time unit)."""
    parsed = dt.datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return int(parsed.timestamp() * 1000)


def _escape_pattern(term: str) -> str:
    """Escape a term for embedding in a double-quoted CloudWatch filter
    pattern (only the quote + backslash are special inside quotes)."""
    return term.replace("\\", "\\\\").replace('"', '\\"')


def _event_to_line(event: dict[str, Any], *, fallback_namespace: str) -> LogLine:
    """Map one CloudWatch ``FilterLogEvents`` event onto a ``LogLine``.

    Container Insights ``application`` logs are JSON envelopes carrying
    ``kubernetes`` metadata + the raw ``log`` line; plain log groups
    carry the message verbatim with pod identity only in the stream
    name. Handle both: parse the JSON envelope when present, else fall
    back to the stream name for pod identity and the raw message."""
    raw_message = event.get("message", "") or ""
    stream = event.get("logStreamName", "") or ""

    namespace = fallback_namespace
    pod = stream
    container = ""
    message = raw_message

    parsed = _try_json(raw_message)
    if isinstance(parsed, dict):
        kube = parsed.get("kubernetes")
        if isinstance(kube, dict):
            namespace = kube.get("namespace_name") or namespace
            pod = kube.get("pod_name") or pod
            container = kube.get("container_name") or container
        # Fluent Bit stores the line under "log"; some agents use "message".
        line = parsed.get("log")
        if not isinstance(line, str):
            line = parsed.get("message")
        if isinstance(line, str):
            message = line

    # CloudWatch timestamps are epoch milliseconds. Emit ISO-8601 so the
    # resolver's ``_ns_to_iso`` passes it through untouched (it only
    # reinterprets all-digit strings as Loki nanoseconds).
    ts_ms = event.get("timestamp")
    if isinstance(ts_ms, int | float):
        timestamp = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.UTC).isoformat()
    else:
        timestamp = str(ts_ms or "")

    return LogLine(
        timestamp=timestamp,
        namespace=namespace,
        pod=pod,
        container=container,
        message=message.rstrip("\n"),
        level=None,
        labels=None,
    )


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None
