import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Cluster prerequisites · Documentation · Astrolift",
};

const certManagerInstall = `# cert-manager v1.13+ (Helm)
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install cert-manager jetstack/cert-manager \\
  --namespace cert-manager --create-namespace \\
  --version v1.15.3 \\
  --set crds.enabled=true

# Or via kubectl (CRDs are bundled in the manifest)
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.3/cert-manager.yaml`;

const ingressNginxInstall = `# ingress-nginx (Helm) — the default Astrolift expects
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \\
  --namespace ingress-nginx --create-namespace`;

const istioInstall = `# Istio Gateway (istioctl)
istioctl install --set profile=default -y
kubectl label namespace default istio-injection=enabled`;

const albInstall = `# AWS Load Balancer Controller (Helm)
helm repo add eks https://aws.github.io/eks-charts
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \\
  --namespace kube-system \\
  --set clusterName=<your-eks-cluster> \\
  --set serviceAccount.create=false \\
  --set serviceAccount.name=aws-load-balancer-controller`;

const externalDnsInstall = `# external-dns (Helm) — Route53 example
helm repo add external-dns https://kubernetes-sigs.github.io/external-dns/
helm install external-dns external-dns/external-dns \\
  --namespace external-dns --create-namespace \\
  --set provider=aws \\
  --set txtOwnerId=<unique-cluster-id> \\
  --set domainFilters[0]=<your-zone-apex>`;

const metricsServerInstall = `# metrics-server — required for HPA
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml`;

const prometheusInstall = `# kube-prometheus-stack (Helm)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack \\
  --namespace monitoring --create-namespace`;

const verifyCertManager = `# Pods Running, three deployments
kubectl get pods -n cert-manager
# NAME                                      READY   STATUS    RESTARTS
# cert-manager-...                          1/1     Running   0
# cert-manager-cainjector-...               1/1     Running   0
# cert-manager-webhook-...                  1/1     Running   0

# CRDs installed
kubectl get crds | grep cert-manager.io
# certificates.cert-manager.io
# certificaterequests.cert-manager.io
# challenges.acme.cert-manager.io
# clusterissuers.cert-manager.io
# issuers.cert-manager.io
# orders.acme.cert-manager.io`;

const verifyIngress = `# An IngressClass must exist — Astrolift renders Ingress with
# spec.ingressClassName matching one of these.
kubectl get ingressclass
# NAME    CONTROLLER                      DEFAULT
# nginx   k8s.io/ingress-nginx            true`;

const verifyStorage = `# At least one StorageClass, ideally one marked default.
kubectl get storageclass
# NAME                 PROVISIONER             RECLAIMPOLICY
# gp3 (default)        ebs.csi.aws.com         Delete`;

const verifyMetrics = `kubectl get apiservice v1beta1.metrics.k8s.io
# NAME                     SERVICE                      AVAILABLE
# v1beta1.metrics.k8s.io   kube-system/metrics-server   True

kubectl top nodes  # should return numbers, not an error`;

export default function ClusterPrerequisitesPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Cluster prerequisites</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Everything a Kubernetes cluster needs to have installed before you
          register it with Astrolift. Most clusters provisioned by{" "}
          <code>astrolift-opscode</code> already include these — this guide is
          for operators bringing their own cluster.
        </p>
      </div>

      <div className="border-primary/30 bg-primary/5 rounded-md border p-4 text-sm">
        <p className="font-medium">One-shot install via the CLI</p>
        <p className="text-muted-foreground mt-1">
          For a one-shot install of the prereqs below — driver-tuned for
          your provider — run{" "}
          <code className="font-mono text-xs">astro cluster bootstrap --cluster-slug &lt;slug&gt;</code>{" "}
          from your terminal. The CLI plants Flux + the platform&apos;s
          umbrella chart, then the cluster detail page&apos;s bootstrap
          card lets you reconcile the selection over time.{" "}
          <Link
            href="/downloads"
            className="text-primary underline-offset-4 hover:underline"
          >
            Install the CLI
          </Link>
          .
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift treats a tenant cluster as a runtime that hosts apps — it
          does not install or own infrastructure components in the cluster
          itself. Custom-domain TLS, ingress routing, and autoscaling are
          delegated to controllers that need to exist before Astrolift can use
          them. The checks below confirm the cluster is wired correctly; if
          any are missing, the matching features stay disabled and the
          control plane surfaces a remediation hint on the cluster detail
          page.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-medium">Required</h2>
          <Badge variant="secondary">must install</Badge>
        </div>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Without these, registration succeeds but app workloads cannot
          accept inbound traffic on a custom hostname.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">
          cert-manager <span className="text-muted-foreground">(v1.13+)</span>
        </h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Issues and renews TLS certificates for custom domains. Astrolift
          creates <code>Certificate</code> resources on your behalf;
          cert-manager handles the ACME dance with Let&apos;s Encrypt (or
          whichever <code>ClusterIssuer</code> you configure). Without
          cert-manager the{" "}
          <Link
            href="/documentation/custom-domains"
            className="text-foreground underline-offset-2 hover:underline"
          >
            custom-domains
          </Link>{" "}
          workflow refuses to bind hostnames.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{certManagerInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Upstream:{" "}
          <a
            href="https://cert-manager.io/docs/installation/"
            className="text-foreground underline-offset-2 hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            cert-manager.io/docs/installation
          </a>
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">An ingress controller</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift renders one ingress resource per app whose
          <code>spec.ingressClassName</code> matches an{" "}
          <code>IngressClass</code> that exists in the cluster. Any of the
          following controllers is fine — pick the one your platform team
          already runs.
        </p>

        <h4 className="text-sm font-medium">
          ingress-nginx <Badge variant="outline">default</Badge>
        </h4>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{ingressNginxInstall}</code>
        </pre>

        <h4 className="text-sm font-medium">Istio Gateway</h4>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{istioInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          When Astrolift detects Istio it emits <code>Gateway</code> +{" "}
          <code>VirtualService</code> resources instead of{" "}
          <code>Ingress</code>.
        </p>

        <h4 className="text-sm font-medium">
          AWS Load Balancer Controller / GCP Gateway API
        </h4>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{albInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          On EKS, the ALB controller exposes services through an{" "}
          <code>alb</code> IngressClass. On GKE, the Gateway API controller
          ships with the cluster — install{" "}
          <a
            href="https://gateway-api.sigs.k8s.io/"
            className="text-foreground underline-offset-2 hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            gateway-api
          </a>{" "}
          CRDs to use it.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">
          external-dns <span className="text-muted-foreground">(optional, strongly recommended)</span>
        </h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Reads the hostnames from{" "}
          <code>Certificate.spec.dnsNames</code> / ingress resources and
          creates the corresponding records at your DNS provider. Required
          when you want Astrolift to fully self-serve custom-domain
          publication — otherwise an operator has to add CNAMEs by hand.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{externalDnsInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Supported providers with controller-friendly APIs include AWS
          Route53, Google Cloud DNS, Azure DNS, and Cloudflare. See{" "}
          <a
            href="https://kubernetes-sigs.github.io/external-dns/latest/#supported-providers"
            className="text-foreground underline-offset-2 hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            the upstream provider list
          </a>{" "}
          for the full set.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-medium">Recommended</h2>
          <Badge variant="outline">nice to have</Badge>
        </div>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift works without these, but specific features stay off
          until they are present.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">A default storage class</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Required for in-cluster managed services that claim{" "}
          <code>PersistentVolumeClaim</code>s — for example the in-cluster
          Postgres operator, in-cluster Redis, or any app that mounts a
          PVC directly. If every app you run uses an external RDS/Cloud SQL
          managed service, you can skip this.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{verifyStorage}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">metrics-server</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The Kubernetes <code>HorizontalPodAutoscaler</code> reads CPU /
          memory from <code>metrics.k8s.io</code>; without metrics-server
          autoscaling rules sit idle at the current replica count.
          Astrolift falls back to manual replica counts but the autoscale
          tab on app detail surfaces a banner explaining what&apos;s
          missing.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{metricsServerInstall}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">In-cluster Prometheus</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          When Astrolift detects a reachable Prometheus it points the app
          metrics panels at it. Without one the panels render synthetic
          time-series so the UI stays useful in dev — but real workload
          numbers require Prometheus or another Prometheus-compatible
          scraper.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{prometheusInstall}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Verifying the install</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Run these from a workstation that can reach the cluster. If any
          check fails, the matching feature stays disabled in Astrolift.
        </p>

        <h3 className="text-sm font-medium">cert-manager</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{verifyCertManager}</code>
        </pre>

        <h3 className="text-sm font-medium">Ingress class</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{verifyIngress}</code>
        </pre>

        <h3 className="text-sm font-medium">metrics-server</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{verifyMetrics}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              cert-manager pending forever (Certificate stuck in
              <code className="mx-1">Pending</code>).
            </strong>{" "}
            Almost always an ACME challenge problem. For{" "}
            <code>dns-01</code>: the issuer has no credentials to write the
            TXT record (check the <code>ClusterIssuer</code> spec, then the
            controller pod logs for credential errors). For{" "}
            <code>http-01</code>: the validator pod cannot reach{" "}
            <code>http://&lt;hostname&gt;/.well-known/acme-challenge/...</code>
            — make sure the ingress controller is exposed publicly. Inspect
            with:
            <pre className="bg-muted mt-2 overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`kubectl describe certificate <name> -n <namespace>
kubectl describe challenge -A
kubectl logs -n cert-manager deploy/cert-manager`}</code>
            </pre>
          </li>
          <li>
            <strong className="text-foreground">
              Ingress returns 503 with no backend.
            </strong>{" "}
            The Service the ingress points at has zero ready endpoints.
            Most often: the app pods are still pulling the image, the
            readiness probe is failing, or the Service{" "}
            <code>selector</code> doesn&apos;t match the pod labels.
            Confirm with{" "}
            <code>kubectl get endpoints &lt;svc&gt;</code>; if the address
            list is empty the ingress has nothing to forward to.
          </li>
          <li>
            <strong className="text-foreground">
              external-dns is not creating records.
            </strong>{" "}
            Two common causes: (a) RBAC — the controller&apos;s service
            account needs <code>list/watch</code> on{" "}
            <code>services</code>, <code>ingresses</code>, and the
            provider&apos;s CRDs, plus IAM/credentials to call the DNS
            provider&apos;s API; (b) ownership — external-dns refuses to
            modify a record it didn&apos;t create unless the TXT owner-id
            marker matches. Check the controller logs for{" "}
            <code>skipping endpoint</code> messages.
          </li>
          <li>
            <strong className="text-foreground">
              HPA shows <code>&lt;unknown&gt;</code> for the target metric.
            </strong>{" "}
            metrics-server is not installed or not scraping. Run{" "}
            <code>kubectl top pods</code> — if it errors with{" "}
            <code>Metrics API not available</code>, metrics-server is the
            fix.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/custom-domains"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Custom domains
            </Link>{" "}
            — the feature that depends on cert-manager and an ingress
            controller being present.
          </li>
          <li>
            <Link
              href="/documentation/get-started"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Get started
            </Link>{" "}
            — bring up the Astrolift stack locally before pointing it at a
            tenant cluster.
          </li>
        </ul>
      </section>
    </article>
  );
}
