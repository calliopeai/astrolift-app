"""CloudWatch Logs LogQueryDriver (#1111).

The historical (time-range) log surface — ``LogQueryDriver`` — for AWS
clusters that ship pod logs through Fluent Bit or the CloudWatch agent
into a Container Insights ``application`` log group. Loki ships the streaming
side; this ships the paginated query side for CloudWatch.

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

The resolver threads the registered cluster credential, including its role
and external ID, through the shared AWS session factory. Without an explicit
credential the control plane's ambient role must carry ``logs:FilterLogEvents``
on the log group. Legacy ``role_arn`` remains supported for ambient registrations;
combining it with an explicit cluster credential is refused. No credential
material is stored in this configuration.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from _sdk.cloud_credentials import CloudCredential

from _sdk.log_stream import LogLine, LogPage

# Pull ``app="x"`` / ``namespace="x"`` / ``workload="x"`` back out of the
# LogQL-flavoured selector ``core.cluster_log_query.build_app_selector``
# emits, so the CloudWatch filter can scope to the app's pods within a
# shared Container Insights log group.
_SELECTOR_LABEL = re.compile(r'(namespace|app|workload)\s*=\s*("(?:[^"\\]|\\.)*")')


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

    credential: CloudCredential | None = None
    """The registered cluster's credential, including its external ID and
    account declaration. Explicit credentials cannot be combined with the
    legacy ``role_arn`` override."""


class CloudWatchLogsQueryDriver:
    """``LogQueryDriver`` backed by CloudWatch Logs ``FilterLogEvents``.

    FilterLogEvents (rather than Logs Insights) keeps the query
    synchronous + cursor-paged, which maps cleanly onto the
    ``LogPage`` contract (Insights is start-query/poll/get-results —
    three round-trips and no native cursor)."""

    def __init__(self, *, config: CloudWatchLogsConfig) -> None:
        self._config = config
        self._logs = config.client

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

        predicates = [
            f"$.kubernetes.namespace_name = {json.dumps(labels['namespace'])}",
            f"$.kubernetes.labels.['astrolift.io/app'] = {json.dumps(labels['app'])}",
        ]
        if "workload" in labels:
            predicates.append(f"$.kubernetes.labels.['astrolift.io/workload'] = {json.dumps(labels['workload'])}")
        kwargs["filterPattern"] = "{ " + " && ".join(predicates) + " }"

        try:
            if self._logs is None:
                self._logs = _build_logs_client(
                    region=self._config.region,
                    role_arn=self._config.role_arn,
                    credential=self._config.credential,
                )
            response = self._logs.filter_log_events(**kwargs)
            items = []
            for event in response.get("events", []):
                line = _event_to_line(event, selector=labels)
                if line is not None and (not search or search.casefold() in line.message.casefold()):
                    items.append(line)
            next_cursor = response.get("nextToken", "") or ""
            if not isinstance(next_cursor, str):
                raise ValueError("Invalid provider cursor")
        except Exception:
            # Callers log provider exceptions. Keep AWS response bodies, request/group
            # identities and malformed event values out of those diagnostics.
            raise RuntimeError("CloudWatch historical log read failed") from None

        return LogPage(
            items=items,
            next_cursor=next_cursor,
            # CloudWatch doesn't cheaply report whether the window
            # predates retention; the resolver treats False as "not
            # known to be truncated", same as Loki.
            reached_retention=False,
            total_count=-1,
        )


class CloudWatchLogsRetentionDriver:
    """Apply a retention window to a CloudWatch log group (#1602).

    CloudWatch is the one observability backend in this tree with a *native*
    retention primitive: ``PutRetentionPolicy`` sets the window and AWS ages
    the data out itself. So "evict per the retention the org configures"
    means setting a policy here, not issuing deletes -- there is nothing to
    delete on a schedule, and a platform-side delete loop would be strictly
    worse than the one AWS already runs.

    That is why this is a separate driver from the query one rather than a
    method on it. The query surface is read-only by construction, and a
    write on it would be reachable from every log-viewing code path.

    Loki, Mimir and Tempo are deliberately absent. Their eviction stories are
    each different (a compactor-mediated delete API, a series-delete
    endpoint, and block retention configured on the compactor rather than
    requested over the wire), so one shared "evict" verb across the four
    would either lie about what it does on three of them or reduce to the
    lowest common denominator. #1602 keeps them, and the AWS-first rule puts
    them after this.
    """

    #: Windows CloudWatch actually accepts. An arbitrary day count is
    #: rejected by the API, so a request is snapped up to the next valid one
    #: -- never down, because keeping data slightly longer than asked is
    #: recoverable and deleting it early is not.
    VALID_DAYS = (
        1,
        3,
        5,
        7,
        14,
        30,
        60,
        90,
        120,
        150,
        180,
        365,
        400,
        545,
        731,
        1096,
        1827,
        2192,
        2557,
        2922,
        3288,
        3653,
    )

    def __init__(self, *, config: CloudWatchLogsConfig) -> None:
        self._config = config
        if config.client is not None:
            self._logs = config.client
        else:
            self._logs = _build_logs_client(
                region=config.region, role_arn=config.role_arn, credential=config.credential
            )

    @classmethod
    def snap_days(cls, days: int) -> int:
        """The smallest accepted window that is at least ``days``.

        Rounding up rather than to nearest: an org asking for 100 days gets
        120, not 90. Under-retaining is a compliance failure and
        over-retaining is a cost line, and only one of those is reversible.
        """
        if days <= 0:
            raise ValueError("retention days must be positive")
        for candidate in cls.VALID_DAYS:
            if candidate >= days:
                return candidate
        return cls.VALID_DAYS[-1]

    def current_retention_days(self) -> int | None:
        """The window on the group now, or None when it never expires.

        None is CloudWatch's own default and its meaning is "keep forever",
        which is the state every group is in until something sets a policy.
        """
        resp = self._logs.describe_log_groups(logGroupNamePrefix=self._config.log_group)
        for group in resp.get("logGroups", []):
            if group.get("logGroupName") == self._config.log_group:
                return group.get("retentionInDays")
        return None

    def apply_retention(self, days: int) -> dict:
        """Set the group's retention window. Idempotent.

        Returns what happened rather than None so the caller can log a real
        change and stay quiet otherwise -- a scheduled task that logs
        "applied retention" every tick trains operators to ignore it.
        """
        wanted = self.snap_days(days)
        current = self.current_retention_days()
        if current == wanted:
            return {"changed": False, "days": wanted}

        self._logs.put_retention_policy(
            logGroupName=self._config.log_group,
            retentionInDays=wanted,
        )
        return {"changed": True, "days": wanted, "previous": current}


def _build_logs_client(*, region: str, role_arn: str | None, credential: CloudCredential | None = None) -> Any:
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

    if credential is not None and not credential.is_ambient:
        if role_arn:
            raise ValueError("CloudWatch logs cannot combine a cluster credential and log_config.role_arn")
        return aws_client("logs", region=region, credential=credential)

    if not role_arn:
        return aws_client("logs", region=region, credential=credential)

    credential = CloudCredential(
        cloud="aws",
        mode=CredentialMode.AWS_ASSUME_ROLE,
        role_arn=role_arn,
        session_name="astrolift-log-query",
    )
    return aws_client("logs", region=region, credential=credential)


def _parse_selector(query: str) -> dict[str, str]:
    """Accept only complete exact Kubernetes identity selectors."""
    error = "CloudWatch logs require exact namespace and app identities"
    if not isinstance(query, str):
        raise ValueError(error)
    selector = query.strip()
    if not selector.startswith("{") or not selector.endswith("}"):
        raise ValueError(error)
    content = selector[1:-1].strip()
    labels = {}
    while content:
        match = _SELECTOR_LABEL.match(content)
        if match is None or match[1] in labels:
            raise ValueError(error)
        try:
            value = json.loads(match[2])
        except ValueError:
            raise ValueError(error) from None
        pattern = (
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            if match[1] == "namespace"
            else r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?"
        )
        if not re.fullmatch(pattern, value):
            raise ValueError(error)
        labels[match[1]] = value
        content = content[match.end() :].strip()
        if content:
            if not content.startswith(",") or not content[1:].strip():
                raise ValueError(error)
            content = content[1:].strip()
    if not {"namespace", "app"}.issubset(labels):
        raise ValueError(error)
    return labels


def _iso_to_ms(iso: str) -> int:
    """ISO-8601 string → epoch milliseconds (CloudWatch's time unit)."""
    parsed = dt.datetime.fromisoformat(iso)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return int(parsed.timestamp() * 1000)


def _event_to_line(event: dict[str, Any], *, selector: dict[str, str]) -> LogLine | None:
    """Only map records admitted by exact collector-stamped Kubernetes metadata."""
    raw_message = event.get("message", "")
    parsed = _try_json(raw_message)
    if not isinstance(parsed, dict):
        return None
    kube = parsed.get("kubernetes")
    if not isinstance(kube, dict):
        return None
    labels = kube.get("labels")
    if not isinstance(labels, dict) or kube.get("namespace_name") != selector["namespace"]:
        return None
    if labels.get("astrolift.io/app") != selector["app"]:
        return None
    if "workload" in selector and labels.get("astrolift.io/workload") != selector["workload"]:
        return None
    # Pod/stream fields describe an already admitted record; neither grants ownership.
    pod = kube.get("pod_name") or event.get("logStreamName") or ""
    container = kube.get("container_name") or ""
    if not isinstance(pod, str) or not isinstance(container, str):
        return None
    message = parsed.get("log")
    if not isinstance(message, str):
        message = parsed.get("message")
    if not isinstance(message, str):
        message = raw_message
    ts_ms = event.get("timestamp")
    if isinstance(ts_ms, int | float):
        timestamp = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.UTC).isoformat()
    else:
        timestamp = str(ts_ms or "")
    return LogLine(
        timestamp=timestamp,
        namespace=kube["namespace_name"],
        pod=pod,
        container=container,
        message=message.rstrip("\n"),
        level=None,
        labels={key: value for key, value in labels.items() if isinstance(key, str) and isinstance(value, str)},
    )


def _try_json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous event metadata")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique)
    except (ValueError, TypeError):
        return None
