# Environments that share a namespace

Since #1922, a new environment created on a cluster where another environment
of the same app already renders gets its own Kubernetes namespace. Environments
created before that keep a blank `k8s_namespace` and render into the app
namespace. Two such environments on one cluster (typically production and
staging from repo bootstrap) overwrite each other's Deployments, Services,
Secrets and PVCs on every deploy.

Preconditions: a shell in the backend container for `manage.py`; `app.deploy` on
the app to deploy the moved environment and redeploy the primary afterwards.
Placeholders: `<slug>` and `<org>` are the organization slug (`slug` from
`astroliftOrganizations`); `<app>` is the registered app slug (`astro app list`);
`<environment>` is a non-primary environment `name` from
`astroliftEnvironmentsPage(appSlug: "<app>")`.

Find them with:

```
python manage.py report_shared_environment_namespaces [--organization <slug>] [--json]
```

Each line names the organization, app, cluster, the shared namespace and the
environments that render into it. The command reads only and exits 1 when it
finds any, so a check can gate on it.

Moving a non-primary environment into its own namespace moves a live
environment, so it runs in operator steps (#2094):

```
python manage.py move_environment_namespace <org>/<app> <environment>
python manage.py move_environment_namespace <org>/<app> <environment> --apply
python manage.py move_environment_namespace <org>/<app> <environment> --clean-up [--apply]
```

The first call prints the plan and changes nothing. `--apply` records the new
namespace; nothing on the cluster changes until you deploy the moved
environment (it renders into the new namespace) and redeploy the primary.
`--clean-up` deletes the moved environment's literal `[env]` Secret from the
app namespace and is refused while a workload there still references it.

## Verify

```sh
python manage.py report_shared_environment_namespaces --organization <slug>; echo "exit $?"
./run.sh test astrolift_lifecycle/tests/test_shared_namespace_report_2094.py astrolift_lifecycle/tests/test_environment_namespace_move_2094.py
```

After the move and both deploys the report prints no rows and exits `0`, and both
test modules pass.
