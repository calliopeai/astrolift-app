# Environments that share a namespace

Since #1922, a new environment created on a cluster where another environment
of the same app already renders gets its own Kubernetes namespace. Environments
created before that keep a blank `k8s_namespace` and render into the app
namespace. Two such environments on one cluster (typically production and
staging from repo bootstrap) overwrite each other's Deployments, Services,
Secrets and PVCs on every deploy.

Find them with:

```
python manage.py report_shared_environment_namespaces [--organization <slug>] [--json]
```

Each line names the organization, app, cluster, the shared namespace and the
environments that render into it. The command reads only and exits 1 when it
finds any, so a check can gate on it.

Moving a non-primary environment into its own namespace is not automatic: it
moves a live environment. Until the operator-driven move lands (#2094), keep
such environments on separate clusters or delete the redundant one.
