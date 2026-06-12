import Link from "next/link";

import { Separator } from "@/components/ui/separator";
import { ArrowLeftIcon } from "lucide-react";

export const metadata = {
  title: "Cluster management · Runbooks · Documentation · Astrolift",
};

const preflightChecks = `kubectl get pods -n cert-manager          # cert-manager running
kubectl get ingressclass                  # IngressClass exists
kubectl get storageclass                  # default StorageClass
kubectl get apiservice v1beta1.metrics.k8s.io  # metrics-server healthy`;

const verifyRegistration = `# Verify from the CLI after registration:
astro clusters status <cluster-slug>

# List apps currently deployed to the cluster:
astro apps list --cluster <cluster-slug>`;

const decommissionPrecheck = `# List apps deployed to the cluster — must be empty before decommission:
astro apps list --cluster <cluster-slug>

# No active deploys:
astro deployments list --cluster <cluster-slug> --status running`;

const labelExamples = `# Standard label set — apply via Cluster detail → Settings → Labels
env      = production
provider = aws
region   = us-west-2
tier     = standard`;

export default function ClusterManagementRunbookPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <Link
          href="/documentation/runbooks"
          className="text-muted-foreground hover:text-foreground mb-4 inline-flex items-center gap-1.5 text-sm transition-colors"
        >
          <ArrowLeftIcon className="h-3.5 w-3.5" />
          Back to runbooks
        </Link>
        <h1 className="text-2xl font-semibold">Cluster management</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Bring a cluster into Astrolift management, maintain it over time,
          and decommission it when it is no longer needed.
        </p>
      </div>

      <Separator />

      <section className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Bring a cluster into management</h2>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 1 — Verify prerequisites</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Before registering, confirm the required components are in place.
            See the full{" "}
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-foreground underline-offset-2 hover:underline"
            >
              cluster prerequisites guide
            </Link>{" "}
            for install instructions.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{preflightChecks}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 2 — Register in Astrolift</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Go to{" "}
            <Link
              href="/clusters"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Clusters
            </Link>{" "}
            and click <strong>Register cluster</strong>. Fill in the name,
            provider, region, and paste the kubeconfig. Click{" "}
            <strong>Register</strong>. Astrolift runs a preflight check
            automatically.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 3 — Review preflight results</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            The cluster detail page shows a bootstrap card. Green means
            detected; amber means recommended but missing; red means required
            and missing. Missing required components block app deploys to that
            cluster until they are installed.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 4 — Label the cluster</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Labels classify the cluster for placement policies. Set them from{" "}
            <strong>Cluster detail → Settings → Labels</strong>.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{labelExamples}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Verification</h3>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{verifyRegistration}</code>
          </pre>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Rotate cluster credentials</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          When the kubeconfig or service account token expires or is rotated:
        </p>
        <ol className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>1. Generate a new kubeconfig with the same permission set.</li>
          <li>
            2. Go to <strong>Cluster detail → Settings → Credentials</strong>.
          </li>
          <li>
            3. Click <strong>Rotate credentials</strong> and paste the new
            kubeconfig.
          </li>
          <li>
            4. Click <strong>Save</strong>. Astrolift validates reachability
            immediately; if validation fails, the old credentials remain
            active.
          </li>
        </ol>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Do not revoke the old credentials until Astrolift confirms the new
          ones work.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Decommission a cluster</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Decommissioning removes the cluster from Astrolift&apos;s management.
          It does not delete the Kubernetes cluster itself.
        </p>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Pre-decommission checklist</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Astrolift blocks decommission if any apps are actively deployed to
            the cluster. Migrate or remove all apps first.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{decommissionPrecheck}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Decommission steps</h3>
          <ol className="text-muted-foreground flex flex-col gap-1.5 text-sm">
            <li>1. Go to <strong>Cluster detail → Settings → Danger zone</strong>.</li>
            <li>2. Click <strong>Decommission cluster</strong>.</li>
            <li>3. Type the cluster slug to confirm.</li>
            <li>4. Click <strong>Confirm decommission</strong>.</li>
          </ol>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Astrolift removes the cluster record and purges the kubeconfig from
            the secrets store. Kubernetes-side resources (Deployments, Services,
            Ingresses) created by Astrolift in app namespaces are left in place
            — clean them up directly if needed.
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              Cluster shows Degraded after network maintenance.
            </strong>{" "}
            Wait 2–3 minutes for the health check backoff to clear. If it
            stays Degraded, verify the kubeconfig is still valid and the
            API endpoint is reachable from the Astrolift control plane.
          </li>
          <li>
            <strong className="text-foreground">
              Preflight shows cert-manager missing but it is installed.
            </strong>{" "}
            Astrolift checks for{" "}
            <code>cert-manager.io/v1</code> CRD availability and at least one
            Ready pod in a namespace named <code>cert-manager</code>. If
            your install is in a non-standard namespace, the pod check may
            not find it. Confirm CRDs are present:{" "}
            <code>kubectl get crds | grep cert-manager.io</code>
          </li>
          <li>
            <strong className="text-foreground">
              Preflight shows metrics-server missing but{" "}
              <code>kubectl top nodes</code> works.
            </strong>{" "}
            Astrolift checks the{" "}
            <code>v1beta1.metrics.k8s.io</code> APIService. Run{" "}
            <code>kubectl get apiservice v1beta1.metrics.k8s.io</code> — if
            it shows <code>False</code>, the metrics-server pods are unhealthy.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Cluster prerequisites
            </Link>{" "}
            — install cert-manager, ingress controller, metrics-server.
          </li>
          <li>
            <Link
              href="/documentation/runbooks/incident-response"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Incident response
            </Link>{" "}
            — triage when a cluster goes Degraded or Unreachable.
          </li>
          <li>
            <Link
              href="/documentation/runbooks/deploy"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Deploy workflow
            </Link>{" "}
            — deploy apps to the registered cluster.
          </li>
        </ul>
      </section>
    </article>
  );
}
