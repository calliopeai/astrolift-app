# Agent RuntimeClass

Agent task and box pods can opt into a node sandbox through the
`AGENT_RUNTIME_CLASS` deployment setting. It defaults to an empty string, preserving
the cluster's default runtime. Set the environment variable to the name of a RuntimeClass installed
on every node that can schedule agents, such as `gvisor` for a `runsc` handler.
Both task and box Job templates emit the same `runtimeClassName`.

Preconditions: no Astrolift permission; `AGENT_RUNTIME_CLASS` is an environment
variable of the backend API and worker processes (`backend/config/settings.py`),
set where the control plane is deployed; the RuntimeClass is applied with a
kubeconfig for the agents' cluster, whose control-plane credential can `get`
`node.k8s.io/runtimeclasses`; checking a pod needs `agent.dispatch` to start a
task or box and the same kubeconfig. Placeholders: `<org-slug>` is `slug` from
`astroliftOrganizations` (`astro org current` shows the working one); agent task
and box pods run in namespace `astrolift-agents-<org-slug>`.

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

## Verify

```sh
kubectl get runtimeclass gvisor
kubectl -n astrolift-agents-<org-slug> get pods \
  -o custom-columns=NAME:.metadata.name,RUNTIME:.spec.runtimeClassName,NODE:.spec.nodeName
```

Every task and box pod must list `gvisor` under `RUNTIME`; a dispatch while the
class is missing is refused with `Agent RuntimeClass 'gvisor' is not available
on the target cluster; install it before dispatch, or clear AGENT_RUNTIME_CLASS.`
