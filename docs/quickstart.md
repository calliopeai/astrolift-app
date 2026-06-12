# Quickstart: zero to live URL in under 10 minutes

This guide takes you from a fresh Astrolift installation to a running
application with a live URL. It assumes you have already deployed the
Astrolift control plane (the Django API and Next.js frontend are up
and reachable).

Estimated time: 8 minutes for an app already containerized or in a Git
repo. Add 2-3 minutes if you are connecting a source provider or cluster
for the first time.

---

## Prerequisites

- An Astrolift instance reachable on a hostname (local or remote).
- A Kubernetes cluster that meets the
  [cluster prerequisites](cluster-prerequisites.md) — cert-manager,
  an ingress controller, and a default storage class.
- A container image in a registry Astrolift can pull from, OR source
  code on GitHub or GitLab with a Dockerfile at the root.
- An account with the `app.add` and `cluster.view` permissions
  (Owner and Admin have both by default).

---

## Step 1 — Connect a cluster

If your cluster is already registered, skip to Step 2.

1. Go to **Clusters** in the left sidebar.
2. Click **Register cluster**.
3. Fill in:
   - **Name**: a human-readable label (e.g. `prod-us-west-2`).
   - **Provider**: select the cloud driver that matches your cluster
     (`aws`, `gcp`, `azure`, or `k8s_native` for self-managed).
   - **Kubeconfig**: paste the kubeconfig for the cluster, or if the
     Astrolift control plane runs inside the same cluster, check
     **In-cluster**.
4. Click **Register**. Astrolift runs a preflight check: it verifies
   cert-manager, an ingress class, and metrics-server are present,
   then marks the cluster **Healthy** or surfaces remediation hints
   for anything missing.

The cluster detail page shows live status within about 30 seconds.

---

## Step 2 — Connect a source repository (if deploying from source)

Skip this step if you are deploying from a pre-built image.

1. Go to **Settings → Source providers**.
2. Click **Connect to GitHub** (or GitLab).
3. Follow the on-screen wizard. For GitHub, Astrolift creates a GitHub
   App on your behalf — you approve the permissions on GitHub's side,
   then the connection appears under **Hosts** automatically.
4. Grant the App access to the repository you want to deploy from.

For the full setup walkthrough including GitLab and OAuth App variants,
see [source-providers.md](operators/source-providers.md).

---

## Step 3 — Register your app

1. Go to **Apps** in the left sidebar.
2. Click **Register app**.
3. In the wizard:
   - **Name and slug**: choose a name; the slug becomes the default
     subdomain under your install's base domain.
   - **Source**: choose **Git repository** (select the repo and
     branch connected in Step 2) or **Container image** (paste a
     fully-qualified image reference).
   - **Project**: assign the app to a project (or create one).
4. On the **Manifest** step, Astrolift generates a starter
   `astrolift.toml`. Accept the defaults for now — you can edit them
   after the first deploy.
5. Click **Register app**.

The app appears in the Apps list with status **Registered**.

---

## Step 4 — Deploy

1. Open the app detail page (click the app name in the list).
2. Click **Deploy** in the top-right corner, or for source-connected
   apps, push a commit to the tracked branch — Astrolift picks it up
   via the SCM webhook automatically.
3. The deploy panel opens. Select the target cluster (the one you
   registered in Step 1) and click **Confirm deploy**.

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

- **Logs tab** on the app detail page for application startup errors.
- **Events tab** on the cluster detail page for scheduling failures
  (ImagePullBackOff, CrashLoopBackOff).
- The cluster's ingress controller logs for routing problems.

---

## What to do next

- **Custom domain**: bind a branded hostname from
  **App detail → Domains → Add domain**. See the
  [custom-domains guide](operators/custom-domains.md).
- **Environment variables and secrets**: add them from
  **App detail → Environment**. Secrets are encrypted at rest.
- **Autoscaling**: set CPU/memory thresholds from
  **App detail → Scale**. Requires metrics-server on the cluster.
- **Runbooks**: for day-two operations — rollbacks, cluster
  decommission, incident response — see the
  [operator runbooks](runbooks/).
