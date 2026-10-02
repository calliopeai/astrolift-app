/**
 * Pure data + formatting for the CI setup section (#382, #854, #1210):
 * provider-aware secret names and reference workflow, drift-badge copy,
 * and the relative-time helper. No React, no server.
 */

/**
 * Per-provider CI metadata (#854).
 *
 * The push-credential and registry concepts differ per cloud, so the two
 * provider-specific secret rows and the reference-workflow steps are keyed
 * off the app's default-cluster ``providerPluginSlug`` rather than hardcoded
 * to AWS. Mirrors the ``providerAuthMeta`` map pattern the cluster Settings
 * page (``IngressAuthCard``) uses.
 *
 * ``renderWorkflowSteps`` returns just the provider-variant job steps
 * (configure-credentials + registry-login + build-and-push); the shared
 * checkout / notify steps are templated once in ``renderWorkflowYaml``.
 */
export interface ProviderCiMeta {
  /** GitHub Actions secret name for the push credential. */
  pushCredentialEnv: string;
  /** Tooltip describing what the push-credential secret holds. */
  pushCredentialHint: string;
  /** GitHub Actions secret name for the registry coordinate. */
  registryEnv: string;
  /** Tooltip describing what the registry secret holds. */
  registryHint: string;
  /** Provider-specific job steps that authenticate + push the image. */
  renderWorkflowSteps: () => string;
}

const providerCiMeta: Record<string, ProviderCiMeta> = {
  aws: {
    pushCredentialEnv: "ASTROLIFT_PUSH_ROLE_ARN",
    pushCredentialHint:
      "IAM role the GitHub Actions runner assumes via OIDC to push images. Provisioned by Astrolift on cluster bootstrap.",
    registryEnv: "ASTROLIFT_ECR_URI",
    registryHint:
      "Amazon ECR repository URI this app pushes to. Tag with the commit SHA per build.",
    renderWorkflowSteps: () => "",
  },
  gcp: {
    pushCredentialEnv: "ASTROLIFT_WORKLOAD_IDENTITY_PROVIDER",
    pushCredentialHint:
      "Workload Identity Federation provider resource the runner authenticates against via OIDC. Provisioned by Astrolift on cluster bootstrap.",
    registryEnv: "ASTROLIFT_ARTIFACT_REGISTRY",
    registryHint:
      "Google Artifact Registry path this app pushes to (e.g. us-docker.pkg.dev/PROJECT/REPO/app). Tag with the commit SHA per build.",
    renderWorkflowSteps: () => `      - name: Authenticate to Google Cloud (OIDC)
        id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: \${{ secrets.ASTROLIFT_WORKLOAD_IDENTITY_PROVIDER }}
          token_format: access_token

      - name: Login to Artifact Registry
        uses: docker/login-action@v3
        with:
          registry: \${{ secrets.ASTROLIFT_ARTIFACT_REGISTRY }}
          username: oauth2accesstoken
          password: \${{ steps.auth.outputs.access_token }}

      - name: Build and push image
        env:
          IMAGE: \${{ secrets.ASTROLIFT_ARTIFACT_REGISTRY }}:\${{ github.sha }}
        run: |
          docker build -t "$IMAGE" .
          docker push "$IMAGE"`,
  },
  azure: {
    pushCredentialEnv: "ASTROLIFT_AZURE_CLIENT_ID",
    pushCredentialHint:
      "Entra ID application (client) id with a GitHub federated credential. The runner logs in via OIDC — no client secret stored. Provisioned by Astrolift on cluster bootstrap.",
    registryEnv: "ASTROLIFT_ACR_LOGIN_SERVER",
    registryHint:
      "Azure Container Registry login server this app pushes to (e.g. myregistry.azurecr.io). Tag with the commit SHA per build.",
    renderWorkflowSteps: () => `      - name: Azure login (OIDC)
        uses: azure/login@v2
        with:
          client-id: \${{ secrets.ASTROLIFT_AZURE_CLIENT_ID }}
          tenant-id: \${{ secrets.ASTROLIFT_AZURE_TENANT_ID }}
          subscription-id: \${{ secrets.ASTROLIFT_AZURE_SUBSCRIPTION_ID }}

      - name: Login to ACR
        run: az acr login --name "\${{ secrets.ASTROLIFT_ACR_LOGIN_SERVER }}"

      - name: Build and push image
        env:
          IMAGE: \${{ secrets.ASTROLIFT_ACR_LOGIN_SERVER }}:\${{ github.sha }}
        run: |
          docker build -t "$IMAGE" .
          docker push "$IMAGE"`,
  },
  k8s_native: {
    pushCredentialEnv: "ASTROLIFT_REGISTRY_PASSWORD",
    pushCredentialHint:
      "Registry password or robot-account token used for docker login. Pair it with an ASTROLIFT_REGISTRY_USERNAME secret on the repo.",
    registryEnv: "ASTROLIFT_REGISTRY_URI",
    registryHint:
      "Container registry URI this app pushes to. Any OCI-compliant registry the cluster can pull from. Tag with the commit SHA per build.",
    renderWorkflowSteps: () => `      - name: Login to registry
        uses: docker/login-action@v3
        with:
          registry: \${{ secrets.ASTROLIFT_REGISTRY_URI }}
          username: \${{ secrets.ASTROLIFT_REGISTRY_USERNAME }}
          password: \${{ secrets.ASTROLIFT_REGISTRY_PASSWORD }}

      - name: Build and push image
        env:
          IMAGE: \${{ secrets.ASTROLIFT_REGISTRY_URI }}:\${{ github.sha }}
        run: |
          docker build -t "$IMAGE" .
          docker push "$IMAGE"`,
  },
};

/** Unbound providers retain familiar secret labels; no AWS workflow is invented. */
export function resolveProviderCiMeta(slug: string): ProviderCiMeta {
  return providerCiMeta[slug] ?? providerCiMeta.aws;
}

/** Badge presentation per drift state. Unknown states fall back to the
 *  neutral muted chip rather than blanking out. */
export const DRIFT_BADGE: Record<string, { label: string; className: string }> = {
  in_sync: { label: "matches", className: "bg-success/10 text-success-fg" },
  template_stale: { label: "template behind", className: "bg-warning/10 text-warning-fg" },
  repo_drift: { label: "edited in repo", className: "bg-warning/10 text-warning-fg" },
  conflict: { label: "both changed", className: "bg-danger/10 text-danger-fg" },
  absent: { label: "not in repo", className: "bg-muted text-muted-foreground" },
  unknown: { label: "unknown", className: "bg-muted text-muted-foreground" },
};

/** One-line, human plain-English gloss under the badge so an operator
 *  knows what the state means and which action resolves it. */
export const DRIFT_HINT: Record<string, string> = {
  in_sync: "The repo's workflow file matches what the platform renders. Nothing to do.",
  template_stale:
    "The repo file is untouched, but the platform renders a newer template. Push to update it.",
  repo_drift:
    "Someone edited the repo's file. Pull to keep their version, or push to replace it through a PR.",
  conflict:
    "The repo file and the template both changed. Pull to keep the repo's version, or push to replace it through a PR.",
  absent: "No workflow file on the deploy branch. Push to create one.",
  unknown: "The repo couldn't be read on the last check. Check again to retry.",
};

/**
 * Reference GitHub Actions workflow (#382, #854).
 *
 * The shared scaffold (checkout + the platform-notify POST) is templated
 * once here; the provider-variant credentials + registry-login + build
 * steps come from ``meta.renderWorkflowSteps()`` so a GCP/Azure/raw-k8s
 * cluster gets a runnable workflow rather than AWS-specific steps. Keyed
 * off the ``ASTROLIFT_*`` secret names rendered above so the operator can
 * paste it verbatim. The commit SHA is the image tag; the deploy webhook
 * POST tells the platform which image to roll out. Matches the contract
 * documented in spec 09 §6.
 */
export function renderWorkflowYaml(
  meta: ProviderCiMeta,
  {
    providerSlug,
    renderedText,
    deployBranch,
  }: { providerSlug: string; renderedText?: string | null; deployBranch?: string | null }
): string | null {
  if (providerSlug === "aws" || !(providerSlug in providerCiMeta)) {
    return renderedText?.trim() ? renderedText : null;
  }
  return `name: astrolift deploy

on:
  push:
    branches: [${JSON.stringify(deployBranch?.trim() || "main")}]
  workflow_dispatch: {}

permissions:
  id-token: write
  contents: read

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

${meta.renderWorkflowSteps()}

      - name: Notify Astrolift
        env:
          API_URL: \${{ secrets.ASTROLIFT_API_URL }}
          APP_SLUG: \${{ secrets.ASTROLIFT_APP_SLUG }}
          TOKEN: \${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}
        run: |
          code=$(curl -sS -o /tmp/astrolift-deploy-response.json -w '%{http_code}' -X POST "$API_URL/api/cli/v1/apps/$APP_SLUG/deploy/" \\
            -H "Authorization: Bearer $TOKEN" \\
            -H "Content-Type: application/json" \\
            -d "{\\"image_tags\\":{\\"*\\":\\"\${{ github.sha }}\\"},\\"commit_sha\\":\\"\${{ github.sha }}\\",\\"branch\\":\\"\${{ github.ref_name }}\\",\\"trigger_kind\\":\\"ci\\"}")
          cat /tmp/astrolift-deploy-response.json; echo
          case "$code" in 2*) ;; *) echo "Astrolift deploy notification FAILED (HTTP $code)"; exit 1;; esac
`;
}

/**
 * Compact relative-time formatter for the webhook-install chip.
 *
 * The Settings page already uses ``useFormatters`` for resync state,
 * but threading that hook through every action component crosses the
 * "props in, JSX out" boundary the section component aims for. A
 * local helper keeps this fragment self-contained.
 */
export function formatRelativeWebhookInstall(
  iso: string,
  translate?: (key: string, values?: { count: number }) => string
): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return translate ? translate("justNow") : "just now";
  const seconds = Math.max(0, Math.floor(diff / 1000));
  if (seconds < 60) return translate ? translate("justNow") : "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return translate ? translate("minutes", { count: minutes }) : `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return translate ? translate("hours", { count: hours }) : `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return translate ? translate("days", { count: days }) : `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return translate ? translate("months", { count: months }) : `${months}mo ago`;
  return translate
    ? translate("years", { count: Math.floor(days / 365) })
    : `${Math.floor(days / 365)}y ago`;
}
