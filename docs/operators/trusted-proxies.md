# Client addresses behind proxies

Per-IP limits key on the request's client address: sign-in (10 a minute),
the invitation preview (30 a minute), local sign-in and the CLI device flow.
Behind a load balancer every request reaches the backend from the balancer,
so the backend must be told which peers it may believe when they name the
client in `X-Forwarded-For` (#2431).

Preconditions: no Astrolift permission; `ASTROLIFT_TRUSTED_PROXIES` is an
environment variable of the backend process (`backend/config/proxies.py`), set
where the control plane is deployed (the ECS task definition or the EKS chart
values of the install) with the balancer's subnet ranges read from the
cluster's AWS account; confirming the parsed value needs a shell in the backend
container. Placeholders: `<cidr>` is the CIDR of a subnet the load balancer is
attached to; the value is a comma-separated list of them.

```sh
ASTROLIFT_TRUSTED_PROXIES=<cidr>,<cidr>
```

`ASTROLIFT_TRUSTED_PROXIES` lists those peers: IP addresses or CIDR ranges,
comma-separated. Loopback (`127.0.0.1`, the nginx sidecar) is always
trusted. `config.asgi` reads `X-Forwarded-For` and `X-Forwarded-Proto` only
when the peer is listed, walks `X-Forwarded-For` from the right, and stops at
the first address it does not trust. That address is the client: entries a
client wrote into the header itself sit to its left and are never read.
uvicorn runs with `--no-proxy-headers`, so its own `FORWARDED_ALLOW_IPS` has
no effect.

Everything else that records or checks the client's address reads the same
resolved address (#2440): the audit trail, session tracking, API and deploy
token "last used from", form submissions, service audit rows and ABAC IP
rules. None of them reads `X-Forwarded-For` itself. Until the setting lists
the balancer's ranges, they all record the balancer's address, and an ABAC IP
allowlist of client ranges denies.

The app refuses to start when the setting holds `*`, a `/0` range, or an
entry that is not an address or range. Unset, only loopback is trusted, as
before: every client behind an ALB then shares the ALB node's budget.

## What to set

| Topology | Peer chain | Value |
| --- | --- | --- |
| ECS webservice with the nginx sidecar (installer `astrolift-deployment-ecs`, astrolift-opscode, SteadyMD) | client, ALB, nginx on loopback, uvicorn | The CIDR ranges of the subnets the ALB is in |
| Control plane on EKS behind the shared ALB (installer `astrolift-control-plane-eks`) | client, ALB, NodePort, api pod | The ALB's subnets, with the api Service on `externalTrafficPolicy: Local` |
| Behind an NLB without proxy protocol | the NLB replaced the client's address | Nothing to trust; see below |

On EKS the chart's api Service uses `externalTrafficPolicy: Cluster`, so
kube-proxy rewrites the source to a node address. Trusting the node subnets
instead is not safe: every pod on them, agent and app workloads included,
could reach the api directly and choose its own address. With `Local` the
peer stays the ALB node.

An NLB in TCP or TLS passthrough without proxy protocol hands the target its
own address, so the client is gone before nginx or the backend sees the
request. Proxy protocol v2 on the target group, with the receiving proxy
reading it, restores it. SteadyMD's NLB fronts the apps on its EKS cluster;
its control plane sits behind an ALB.

## Checking it

uvicorn's access log shows the client column after the change:

```
INFO:     203.0.113.7:0 - "POST /app/auth1/invitation-preview HTTP/1.1" 200 OK
```

A private balancer address there (`10.x.x.x:0`) means its subnet is not in
the setting. Never list a range that holds hosts other than the proxies:
whatever runs there can name any client address it likes.

## Verify

```sh
# a shell in the backend container, where manage.py lives
python manage.py shell -c "from django.conf import settings; print(settings.TRUSTED_PROXIES)"
```

The tuple must start with `127.0.0.1` and list every `<cidr>` you set; then a
request through the balancer must show the client's own address, not a
`10.x.x.x` balancer address, in the uvicorn access-log line above.
