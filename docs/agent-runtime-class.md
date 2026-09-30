# Agent RuntimeClass

Agent task and box pods can opt into a node sandbox through the
`AGENT_RUNTIME_CLASS` deployment setting. It defaults to an empty string, preserving
the cluster's default runtime. Set the environment variable to the name of a RuntimeClass installed
on every node that can schedule agents, such as `gvisor` for a `runsc` handler.
Both task and box Job templates emit the same `runtimeClassName`.

The control plane reads the cluster-scoped RuntimeClass before applying the
agent Job. A missing, deleting or unreadable class refuses dispatch with a
message naming the class. A box records that failure on its status row. No
agent pod is created on a failed preflight. This checks registration; operators
must still install the handler on eligible nodes and verify it runs there.

For a containerd cluster with gVisor already installed, the registration is:

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
```

Use the node-pool scheduling and overhead fields appropriate to the cluster.
The control-plane credential needs `get` on `node.k8s.io/runtimeclasses`.
After enabling the setting, inspect a dispatched task and a box pod: both must
have `spec.runtimeClassName: gvisor` and run on a node whose containerd handler
is `runsc`. Verify its sandbox with gVisor's own runtime diagnostics.

The existing network fence, model gateway, resource limits, seccomp profile and
service-account token restrictions continue to apply. A RuntimeClass setting
alone does not establish that an agent is eligible for interactive AHP attach;
the host must independently check all required controls on the running workload.
