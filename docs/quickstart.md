# Quickstart: zero to live URL in under 10 minutes

This guide takes you from a fresh Astrolift installation to a running
application with a live URL. It assumes you have already deployed the
Astrolift control plane (the Django API and Next.js frontend are up
and reachable).

Preconditions: `cluster.register` to register the cluster, `app.create` to
register the app and `app.deploy` to deploy (`backend/core/permissions.py`);
console routes `/clusters/new`, `/providers`, `/apps/new`, `/apps/<app-slug>`
and `/apps/<app-slug>/deployments`; a kubeconfig or ServiceAccount token for
the target cluster, read from the cluster's cloud account; the `astro` CLI
signed in with `astro auth login` for the verification step. Placeholders:
`<app-slug>` is the Slug the Run step derives, also `slug` in `astro app list`;
`<hostname>` is the `Hostname:` line of `astro app show <app-slug>`, or the
host of the environment's `URL` column there.

Estimated time: 8 minutes for an app already containerized or in a Git
repo. Add 2-3 minutes if you are connecting a source provider or cluster
for the first time.

---

## Prerequisites

- An Astrolift instance reachable on a hostname (local or remote).
- A Kubernetes cluster that meets the cluster prerequisites (the console
  page at `/documentation/cluster-prerequisites`): cert-manager,
  an ingress controller, and a default storage class.
- Source code on GitHub or GitLab with an `astrolift.toml` manifest and a
  Dockerfile; the register wizard deploys from a connected repository.
- An account with the `cluster.register` and `app.create` permissions
  (Organization Owner and Organization Admin have both; Cluster Owner
  registers clusters, Team Owner, Team Admin and Project Admin create apps).

---

## Step 1 — Connect a cluster

If your cluster is already registered, skip to Step 2.

1. Go to **Clusters** in the left sidebar.
2. Click **Register cluster**. The page has two steps, **Cluster** and
   **Connection**.
3. Fill in:
   - **Display name** and **Slug**: a human-readable label (e.g.
     `prod-us-west-2`) and its URL-safe identifier.
   - **Provider plugin**: the driver that matches your cluster (`aws`,
     `gcp`, `azure`, or `k8s_native` for self-managed), plus a **Region**
     for the cloud plugins.
   - **API endpoint**, **Auth method** and **Auth config (JSON)**: a
     kubeconfig (use **Paste kubeconfig** to convert a raw kubeconfig), a
     ServiceAccount token, or the exec plugin that cloud-managed clusters
     use to mint tokens.
4. Click **Register cluster**. The control plane probes the cluster's
   capabilities on register (Kubernetes version, cert-manager,
   external-dns, installed operators) and stores the result on the
   cluster.

The cluster's **Status** and **Health** tabs show what the probe found.

---

## Step 2 — Connect a source repository (if deploying from source)

Skip this step if you are deploying from a pre-built image.

1. Go to **Providers** in the left sidebar; source connections are in the
   **Source providers** section.
2. Click **Connect to GitHub** (or **Connect to GitLab**).
3. Follow the on-screen wizard. For GitHub, Astrolift creates a GitHub
   App on your behalf — you approve the permissions on GitHub's side,
   then the connection appears under **Hosts** automatically.
4. Grant the App access to the repository you want to deploy from.

For the full setup walkthrough including GitLab and OAuth App variants,
see the console page at `/documentation/source-providers` and
[scm-github-oauth.md](operators/scm-github-oauth.md).

---

## Step 3 — Register your app

1. Go to **Apps** in the left sidebar.
2. Click **Register app**.
3. In the wizard, three steps:
   - **Source**: pick the repository and branch connected in Step 2.
     Astrolift fetches the repo's `astrolift.toml`; if there is none,
     paste one (a top-level `name` and at least one `[[workloads]]`
     block are required) or choose **Set up manifest later**.
   - **Run**: choose a **Name** (the **Slug** is derived from it and
     becomes the default subdomain under your install's base domain),
     pick the **Project** the app belongs to, then how and when it
     deploys (**Deploy now** triggers the first deploy on register).
   - **Review**: check what will be created, then click **Register app**.

The app appears in the Apps list as **Pending**, then **Provisioning**,
then **Live**.

---

## Step 4 — Deploy

1. Open the app detail page (click the app name in the list).
2. Click **Deploy** in the header, or push a commit to the tracked
   branch; Astrolift picks it up via the SCM webhook automatically.
3. The dialog **Deploy <tag> to <env>?** redeploys the most recent image
   tag to the app's environment (the environment carries the cluster
   registered in Step 1); click **Deploy**. Use the **Environments** page
   to pick a different tag or environment.

Astrolift queues a build (if deploying from source), pushes the image
to the cluster, and applies the manifest. The deploy log streams in
real time on the deploy detail page.

A typical deploy from a containerized app takes 20-40 seconds for
scheduling and image pull. A source build adds the build duration on
top.

---

## Step 5 — Verify: live URL in the console

1. Once the deploy status shows **Running**, click the **Open** link
   on the app detail page (or look for the hostname in the
   **Domains** section).
2. The URL follows the pattern
   `<app-slug>.<cluster-base-domain>` by default. If you have
   external-dns wired up, the record propagates within a few seconds
   of the ingress being created.
3. Visit the URL. You should see your application.

If the app is not reachable within 2 minutes of the deploy completing,
check:

- **Logs & metrics** tab on the app detail page for application
  startup errors.
- **Status** and **Activity** tabs on the cluster detail page for
  scheduling failures (ImagePullBackOff, CrashLoopBackOff).
- The cluster's ingress controller logs for routing problems.

## Verify

```sh
astro app show <app-slug>
curl -I https://<hostname>
```

`astro app show` must print `Status:       active`, a `Hostname:` (or
`Subdomain:`) line and the environment's `URL`; `curl -I` against that hostname must return
an HTTP 200 status line from your application.

---

## What to do next

- **Custom domain**: bind a branded hostname from
  **App detail → Domains → Add domain**. See the console page at
  `/documentation/custom-domains`.
- **Environment variables and secrets**: add them from
  **App detail → Secrets** and **Config**. Secrets are encrypted at rest.
- **Autoscaling**: set `hpa_min`, `hpa_max` and `hpa_target_cpu_pct` on
  the workload in `astrolift.toml` (also editable under **Config**); the
  workload page shows the HPA. Requires metrics-server on the cluster.
  See [workload and node autoscaling](operators/workload-and-node-autoscaling.md).
- **Runbooks**: for day-two operations — rollbacks, cluster
  decommission, incident response — see the
  [operator runbooks](runbooks/).
