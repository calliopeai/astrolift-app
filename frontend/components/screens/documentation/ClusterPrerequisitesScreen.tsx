import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

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

const code = (chunks: ReactNode) => <code>{chunks}</code>;
function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline-offset-2 hover:underline">
        {chunks}
      </Link>
    );
  };
}
function externalLink(href: string) {
  return function ExternalLink(chunks: ReactNode) {
    return (
      <a
        href={href}
        className="text-foreground underline-offset-2 hover:underline"
        target="_blank"
        rel="noreferrer"
      >
        {chunks}
      </a>
    );
  };
}

/** The static "Cluster prerequisites" documentation page. */
export function ClusterPrerequisitesScreen() {
  const t = useTranslations("docsClusterPrereqs");
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t.rich("intro", { code })}</p>
      </div>

      <div className="border-primary/30 bg-primary/5 rounded-md border p-4 text-sm">
        <p className="font-medium">{t("cliTitle")}</p>
        <p className="text-muted-foreground mt-1">
          {t.rich("cliBody", {
            command: "astro cluster bootstrap --cluster-slug <slug>",
            cmd: (chunks) => <code className="font-mono text-xs">{chunks}</code>,
            link: (chunks) => (
              <Link href="/downloads" className="text-primary underline-offset-4 hover:underline">
                {chunks}
              </Link>
            ),
          })}
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("whenTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("whenBody")}</p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-medium">{t("requiredTitle")}</h2>
          <Badge variant="secondary">{t("requiredBadge")}</Badge>
        </div>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("requiredBody")}</p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">
          cert-manager <span className="text-muted-foreground">(v1.13+)</span>
        </h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("certManagerBody", {
            code,
            link: docLink("/documentation/custom-domains"),
          })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{certManagerInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t("upstream")}{" "}
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
        <h3 className="text-base font-medium">{t("ingressTitle")}</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("ingressBody", { code })}
        </p>

        <h4 className="text-sm font-medium">
          ingress-nginx <Badge variant="outline">{t("defaultBadge")}</Badge>
        </h4>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{ingressNginxInstall}</code>
        </pre>

        <h4 className="text-sm font-medium">Istio Gateway</h4>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{istioInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("istioNote", { code })}
        </p>

        <h4 className="text-sm font-medium">AWS Load Balancer Controller / GCP Gateway API</h4>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{albInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("albBody", { code, link: externalLink("https://gateway-api.sigs.k8s.io/") })}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">
          external-dns <span className="text-muted-foreground">{t("externalDnsQualifier")}</span>
        </h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("externalDnsBody", { code })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{externalDnsInstall}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("externalDnsProviders", {
            link: externalLink(
              "https://kubernetes-sigs.github.io/external-dns/latest/#supported-providers"
            ),
          })}
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-medium">{t("recommendedTitle")}</h2>
          <Badge variant="outline">{t("recommendedBadge")}</Badge>
        </div>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("recommendedBody")}</p>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">{t("storageTitle")}</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("storageBody", { code })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{verifyStorage}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">metrics-server</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("metricsBody", { code })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{metricsServerInstall}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">{t("prometheusTitle")}</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("prometheusBody")}</p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{prometheusInstall}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("verifyTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("verifyBody")}</p>

        <h3 className="text-sm font-medium">cert-manager</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{verifyCertManager}</code>
        </pre>

        <h3 className="text-sm font-medium">{t("ingressClassTitle")}</h3>
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
        <h2 className="text-lg font-medium">{t("troubleshootingTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              {t.rich("troubleCertTitle", {
                pending: (chunks) => <code className="mx-1">{chunks}</code>,
              })}
            </strong>{" "}
            {t.rich("troubleCertBody", {
              code,
              url: "http://<hostname>/.well-known/acme-challenge/...",
            })}
            <pre className="bg-muted mt-2 overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`kubectl describe certificate <name> -n <namespace>
kubectl describe challenge -A
kubectl logs -n cert-manager deploy/cert-manager`}</code>
            </pre>
          </li>
          <li>
            <strong className="text-foreground">{t("troubleIngressTitle")}</strong>{" "}
            {t.rich("troubleIngressBody", { code, command: "kubectl get endpoints <svc>" })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleDnsTitle")}</strong>{" "}
            {t.rich("troubleDnsBody", { code })}
          </li>
          <li>
            <strong className="text-foreground">
              {t.rich("troubleHpaTitle", { code, unknown: "<unknown>" })}
            </strong>{" "}
            {t.rich("troubleHpaBody", { code })}
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("relatedTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            {t.rich("relatedCustomDomains", { link: docLink("/documentation/custom-domains") })}
          </li>
          <li>{t.rich("relatedGetStarted", { link: docLink("/documentation/get-started") })}</li>
        </ul>
      </section>
    </article>
  );
}
