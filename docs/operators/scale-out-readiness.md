# Scale-out readiness

What the control plane provides so the installer can autoscale the Astrolift AI
webservice and Temporal worker (#2435, child of calliope-installer#506): a
backlog signal for the worker, a drain on scale-in, database connections that
stay inside the Aurora budget, and no per-process state that breaks with more
than one replica.

## Backlog signal

`python manage.py publish_task_queue_metrics` reads every task queue the worker
polls (`astrolift.deploy`, `astrolift.provision`, `astrolift.observability` and
`TEMPORAL_TASK_QUEUE`, default `astrolift-main`) with Temporal
`DescribeTaskQueue` backlog stats and publishes two values per queue. Workflow
and activity backlogs are combined: the count is their sum, the age the older of
the two. The age is how long the oldest waiting task has gone unstarted, the
schedule-to-start latency a new task will see.

| Value | CloudWatch (namespace `Astrolift/Temporal`) | Prometheus |
|---|---|---|
| Tasks waiting | `ApproximateBacklogCount`, unit Count | `astrolift_temporal_task_queue_backlog` |
| Age of the oldest waiting task | `ApproximateBacklogAgeSeconds`, unit Seconds | `astrolift_temporal_task_queue_backlog_age_seconds` |

CloudWatch dimensions are `TaskQueue` and `TemporalNamespace`; Prometheus
labels are `task_queue` and `temporal_namespace`.

Run one copy per install, either as a sidecar container next to a worker or as
its own small task with the worker image. Every copy publishes the same gauge,
so a duplicate is harmless; scale on the `Maximum` statistic (CloudWatch) or
`max()` (PromQL).

```bash
# ECS: every 30s, needs cloudwatch:PutMetricData on the task role
python manage.py publish_task_queue_metrics --sink cloudwatch --interval 30
# Or from a scheduled task, one sample per run
python manage.py publish_task_queue_metrics --sink cloudwatch --once
# EKS: serves :9464/metrics for Prometheus
python manage.py publish_task_queue_metrics --sink prometheus --port 9464 --interval 15
```

A queue the server returns no stats for is logged and skipped, never published
as zero (a zero would scale workers in). Backlog stats need a Temporal server
that reports them; this was verified against server 1.31. Check the bundled
`auto-setup` image before relying on the metric there.

ECS step scaling on the worker service, for example:

- alarm `Maximum(ApproximateBacklogCount)` over the polled queues above 20 for
  2 minutes: add 1 task, cooldown 60s;
- alarm `Maximum(ApproximateBacklogAgeSeconds)` above 60: add 2 tasks;
- alarm backlog `== 0` for 15 minutes: remove 1 task, down to the footprint floor.

KEDA on EKS:

```yaml
triggers:
  - type: prometheus
    metadata:
      serverAddress: http://prometheus.monitoring:9090
      query: max(astrolift_temporal_task_queue_backlog)
      threshold: "20"
```

KEDA's Temporal scaler, where the cluster's KEDA has it, reads the same
`DescribeTaskQueue` data and is an alternative to the Prometheus trigger.

## Drain on scale-in

On SIGTERM the worker stops polling on every queue at once and gives running
activities `ASTROLIFT_WORKER_DRAIN_SECONDS` (default 25) to finish. Activities
still running when the window closes are cancelled and Temporal retries them on
another worker; an activity that checkpoints in its heartbeat details resumes
from the last one instead of starting over. Before #2435 the window was zero, so
every scale-in cancelled work that was about to complete, and the four queue
workers drained one after another.

- **ECS:** set the worker container's `stopTimeout` above the drain window. The
  default of 30s fits the default 25s window; ECS allows up to 120s, so a 110s
  window needs `stopTimeout = 120`.
- **EKS:** set `terminationGracePeriodSeconds` above the drain window.

A cancelled activity is retried at once only if it reports back. When a task is
killed outright (stop timeout exceeded, host lost), Temporal notices through the
activity's `heartbeat_timeout`, or, without one, only when `start_to_close_timeout`
expires, which is up to 25 minutes for a build. Long activities should heartbeat
and their callers should set `heartbeat_timeout`; several heartbeating
activities do not set it yet.

## Database connections

`DATABASES["default"]` reads:

| Env | Default | Meaning |
|---|---|---|
| `POSTGRES_CONN_MAX_AGE` | `0` | Seconds to reuse a connection. 0 closes it after each request or activity. |
| `POSTGRES_DISABLE_SERVER_SIDE_CURSORS` | `false` | Set `true` behind PgBouncer in transaction mode. |

Health checks are on, so a reused connection that died in a failover is
replaced rather than failing the request.

Connections per task, with `CONN_MAX_AGE = 0`:

- **Webservice** (uvicorn, ASGI): one per sync request in flight, bounded by the
  asgiref thread pool (`ASGI_THREADS`). Django recommends no persistent
  connections under ASGI; keep 0 and let a pooler reuse them.
- **Worker:** one per synchronous activity running, bounded by
  `ASTROLIFT_SYNC_ACTIVITY_THREADS` (default 16), plus a few for async
  activities. Each sync activity now closes its connection when it ends; before
  #2435 each executor thread kept one open for the life of the worker.

Size Aurora `max_connections` (or the proxy's `MaxConnectionsPercent`) for the
fleet's ceiling: `max_tasks x per-task bound`, for webservice and worker each.

**RDS Proxy (recommended).** No application change needed. The control plane
takes session-level advisory locks in a few places (agent task control, the GCP
preparation, workload identity and app apply journals, `migrate`); RDS Proxy
pins those sessions to one database connection, which keeps them correct at the
cost of multiplexing while they run. Prove it on a test install, not a live one,
before switching an install over.

**PgBouncer.** Use session pooling. Transaction pooling is not supported: a
session-level advisory lock taken in one transaction and released in another
can land on different server connections, and the lock leaks. Behind any
PgBouncer mode, the `options` startup parameter Django sends
(`-c search_path=public`) must be allowed or set on the role instead
(`ALTER ROLE <user> SET search_path = public`) with `options` in
`ignore_startup_parameters`.

## Per-process state

Audited for anything held in memory across requests:

| State | Where | With N replicas |
|---|---|---|
| GraphQL subscription events (`deployment.lifecycle.*`) | `core/pubsub.py` | **Moved to Redis.** Set `ASTROLIFT_PUBSUB_REDIS_URL` (the cache Redis works) on the webservice and the worker. Without it, events stay in the process that published them, so a deployment transition recorded by the worker never reaches a browser, even with one replica. |
| Approval token and cron fired events | `core/pubsub.py` | Stay in process on purpose: the approval token event carries the plaintext token and nothing subscribes to either topic. |
| GraphQL rate limits | django-ratelimit on the default cache | Shared: the default cache is Redis (`DJANGO_CACHE_URL`). |
| Runtime config (Constance) | database backend, Redis cache | Shared. |
| Sessions | database | Shared. |
| Notification subscription | polls the database | Shared. |
| Exec and VNC websockets | the connection itself | A stream lives on the replica that accepted it; the load balancer keeps the connection there. |
| AWS STS credentials, GitHub App installation tokens, boto3 and email drivers, licence verification, Prometheus query results (30s) | process caches | Safe: each replica fetches or builds its own; nothing is coordinated through them. |
| Plugin registries, RBAC rule file, RDS CA bundle | process caches | Static per release. |
