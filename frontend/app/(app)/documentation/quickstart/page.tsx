import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Quickstart · Documentation · Astrolift",
};

const step1Connect = `# Verify prerequisites on the cluster before registering:
kubectl get pods -n cert-manager          # cert-manager running
kubectl get ingressclass                  # at least one IngressClass
kubectl get storageclass                  # at least one default StorageClass
kubectl get apiservice v1beta1.metrics.k8s.io  # metrics-server healthy`;

const step3RegisterCli = `# From the CLI — equivalent to the UI wizard:
astro apps register \\
  --name my-app \\
  --source github://my-org/my-repo@main \\
  --project my-project`;

const step4DeployCli = `# Deploy to a specific cluster:
astro deploy --app my-app --cluster prod-us-west-2

# Wait for completion and stream the log:
astro deploy --app my-app --cluster prod-us-west-2 --wait --tail`;

const step5Verify = `# Check the app is Running:
astro apps status my-app

# Fetch the live URL:
astro apps show my-app | grep url

# Or directly:
curl -I https://my-app.<cluster-base-domain>/`;

export default function QuickstartPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <div className="mb-3 flex items-center gap-2">
          <Badge variant="secondary">GA criterion #1</Badge>
          <span className="text-muted-foreground text-xs">under 10 minutes</span>
        </div>
        <h1 className="text-2xl font-semibold">Quickstart</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Zero to a live URL in under 10 minutes. This guide assumes the
          Astrolift control plane is already deployed and reachable.
        </p>
      </div>

      <div className="border-primary/30 bg-primary/5 rounded-md border p-4 text-sm">
        <p className="font-medium">Prerequisites</p>
        <ul className="text-muted-foreground mt-2 flex flex-col gap-1 text-sm">
          <li>
            A Kubernetes cluster that meets the{" "}
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-foreground underline-offset-2 hover:underline"
            >
              cluster prerequisites
            </Link>{" "}
            — cert-manager, an ingress controller, metrics-server.
          </li>
          <li>
            A container image in a registry Astrolift can pull from, or
            source code on GitHub or GitLab with a Dockerfile.
          </li>
          <li>
            An account with <code>app.add</code> and <code>cluster.view</code>{" "}
            permissions (Owner and Admin have both by default).
          </li>
        </ul>
      </div>

      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">
            <span className="text-muted-foreground mr-2 font-normal">Step 1.</span>
            Connect a cluster
          </h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            If your cluster is already registered and healthy, skip to Step 2.
          </p>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Verify the prerequisites are in place, then go to{" "}
            <Link
              href="/clusters"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Clusters
            </Link>{" "}
            and click <strong>Register cluster</strong>. Fill in the name,
            provider, and kubeconfig. Astrolift runs a preflight check and
            marks the cluster <strong>Healthy</strong> within 30 seconds.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{step1Connect}</code>
          </pre>
          <p className="text-muted-foreground text-sm leading-relaxed">
            The cluster detail page shows a bootstrap card with remediation
            hints for any missing components.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">
            <span className="text-muted-foreground mr-2 font-normal">Step 2.</span>
            Connect a source repository
          </h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Skip this step if you are deploying from a pre-built container
            image.
          </p>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Go to{" "}
            <Link
              href="/settings/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Settings · Source providers
            </Link>{" "}
            and click <strong>Connect to GitHub</strong> (or GitLab).
            Astrolift creates a GitHub App on your behalf — approve the
            permissions on GitHub&apos;s side and the connection appears under
            Hosts automatically. For the detailed GitLab and OAuth App flows,
            see{" "}
            <Link
              href="/documentation/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Source providers
            </Link>
            .
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">
            <span className="text-muted-foreground mr-2 font-normal">Step 3.</span>
            Register your app
          </h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Go to{" "}
            <Link
              href="/apps/new"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Apps · Register app
            </Link>{" "}
            and follow the wizard:
          </p>
          <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
            <li>
              <strong className="text-foreground">Name and slug</strong> —
              the slug becomes the default subdomain under the cluster&apos;s
              base domain.
            </li>
            <li>
              <strong className="text-foreground">Source</strong> — choose
              a Git repository (select the repo and branch from Step 2) or
              a container image (paste a fully-qualified image reference).
            </li>
            <li>
              <strong className="text-foreground">Manifest</strong> —
              Astrolift generates a starter <code>astrolift.toml</code>.
              Accept the defaults and edit after the first deploy.
            </li>
          </ul>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{step3RegisterCli}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">
            <span className="text-muted-foreground mr-2 font-normal">Step 4.</span>
            Deploy
          </h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Open the app detail page and click <strong>Deploy</strong>{" "}
            (top-right). Select the cluster from Step 1 and click{" "}
            <strong>Confirm deploy</strong>. The deploy log streams in real
            time.
          </p>
          <p className="text-muted-foreground text-sm leading-relaxed">
            For source-connected apps, a push to the tracked branch triggers
            a deploy automatically via the SCM webhook — no UI action needed.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{step4DeployCli}</code>
          </pre>
          <p className="text-muted-foreground text-sm leading-relaxed">
            A containerized app typically reaches Running in 20–40 seconds.
            A source build adds the build duration on top.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">
            <span className="text-muted-foreground mr-2 font-normal">Step 5.</span>
            Verify: live URL in the console
          </h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Once the deploy status shows <strong>Running</strong>, click the{" "}
            <strong>Open</strong> link on the app detail page. The URL follows
            the pattern <code>{"<app-slug>.<cluster-base-domain>"}</code> by
            default. With external-dns configured, the record propagates within
            a few seconds of the ingress being created.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{step5Verify}</code>
          </pre>
          <p className="text-muted-foreground text-sm leading-relaxed">
            If the app is not reachable within 2 minutes of the deploy
            completing, check the <strong>Logs</strong> and{" "}
            <strong>Events</strong> tabs on the app detail page. Common causes:
            missing required environment variable, image pull failure, or a
            readiness probe returning non-2xx.
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">What to do next</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <Link
              href="/documentation/custom-domains"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Custom domains
            </Link>{" "}
            — bind a branded hostname with auto-managed TLS.
          </li>
          <li>
            <Link
              href="/documentation/runbooks"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Operator runbooks
            </Link>{" "}
            — rollbacks, cluster decommission, incident response, and
            agent dispatch.
          </li>
          <li>
            <Link
              href="/documentation/configuration"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Configuration reference
            </Link>{" "}
            — every environment variable the platform reads.
          </li>
        </ul>
      </section>
    </article>
  );
}
