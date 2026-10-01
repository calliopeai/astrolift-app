import type { AstroliftClusterAuthUser } from "@/graphql/__generated__/schema";

import type { AuthUsersViewProps } from "./AuthUsers";
import type { BootstrapPlanViewProps } from "./BootstrapPlan";
import type { CentralAuthViewProps, IngressClassViewProps } from "./CentralAuth";
import type { ClusterAgentViewProps } from "./ClusterAgent";
import type { BootstrapHistoryViewProps, ClusterSettingsScreenProps } from "./ClusterSettings";
import type { IngressAuthViewProps } from "./IngressAuth";
import type { BootstrapRun, ClusterWithHeartbeat } from "./types";

/** Hand-typed fixtures for the cluster settings tab and its cards. */

const noop = async () => {};
const yes = async () => true;

export const LONG =
  "platform-team-shared-production-workloads-us-west-2-with-a-deliberately-long-name-that-keeps-going";

/** A 64-character SHA, a 200-character ARN and an unbroken URL, for overflow stories. */
export const SHA64 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
export const ARN200 = (
  "arn:aws:cognito-idp:us-west-2:123456789012:userpool/us-west-2_AbCdEf123/" +
  "platform-team-shared-production-workloads-with-a-deliberately-long-pool-path-" +
  "that-keeps-going-and-going-until-it-is-two-hundreds"
).slice(0, 200);
export const UNBROKEN_URL = `https://${"a".repeat(40)}.gr7.us-west-2.eks.amazonaws.com/${"b".repeat(120)}`;

export const BOOTSTRAP_RUN: BootstrapRun = {
  id: "run-3",
  status: "succeeded",
  chartVersion: "astrolift-system-0.14.2",
  installedReleases: [
    { name: "cert-manager", version: "v1.15.3", status: "deployed" },
    { name: "ingress-nginx", version: "4.11.2", status: "deployed" },
    { name: "external-dns", version: "1.15.0", status: "deployed" },
  ],
  cliVersion: "astro 3.3.1",
  errorMessage: "",
  startedAt: "2026-09-27T14:02:00Z",
  endedAt: "2026-09-27T14:05:41Z",
  triggeredByUsername: "leo",
};

export const FAILED_RUN: BootstrapRun = {
  ...BOOTSTRAP_RUN,
  id: "run-2",
  status: "failed",
  installedReleases: [],
  errorMessage:
    'Error: INSTALLATION FAILED: cannot re-use a name that is still in use (release "cert-manager" in namespace "astrolift-system")',
  startedAt: "2026-09-26T09:10:00Z",
  endedAt: "2026-09-26T09:11:12Z",
};

export const CLUSTER = {
  id: "c0ffee00-0000-4000-8000-000000000001",
  slug: "prod-west",
  name: "Production West",
  providerPluginSlug: "aws",
  endpoint: "https://A1B2C3D4E5.gr7.us-west-2.eks.amazonaws.com",
  authMethod: "irsa",
  ingressClass: "alb",
  lifecycle: "managed",
  lastManagementError: "",
  createdAt: "2026-08-02T17:30:00Z",
  capabilitiesProbedAt: "2026-09-27T14:06:00Z",
  capabilities: {
    cert_manager: { installed: true, version: "v1.15.3", default_issuer: "letsencrypt-prod" },
    ingress: { installed: true, class: "alb", controller_version: "v2.8.2" },
    external_dns: { installed: true, provider: "aws" },
    storage_classes: ["gp3", "efs-sc"],
    metrics_server: true,
    prometheus: false,
  },
  lastBootstrapRun: BOOTSTRAP_RUN,
  albAuthConfig: {
    user_pool_arn: "arn:aws:cognito-idp:us-west-2:123456789012:userpool/us-west-2_AbCdEf123",
    user_pool_client_id: "4f8k2m9q1r7t3v5x6z0b",
    user_pool_domain: "prod-west-apps",
  },
  oidcAuthConfig: {
    discovery_url:
      "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEf123/.well-known/openid-configuration",
    client_id: "central-7h3j5k",
    auth_proxy_host: "auth.apps.prod-west.example.com",
    client_secret_set: true,
    cookie_secret_set: true,
    gateway_secret_set: false,
  },
  agentProvisioned: true,
  heartbeatIntervalSeconds: 30,
} as unknown as ClusterWithHeartbeat;

export const SETTINGS: ClusterSettingsScreenProps = {
  slug: CLUSTER.slug,
  cluster: CLUSTER,
  loading: false,
  lifecycle: "managed",
  bringing: false,
  refreshing: false,
  decommissioning: false,
  onBring: noop,
  onRefresh: noop,
  onDecommission: noop,
  access: { manage: true, update: true, users: true, unregister: true },
};

/** A viewer with none of the cluster permissions. */
export const NO_ACCESS: ClusterSettingsScreenProps["access"] = {
  manage: false,
  update: false,
  users: false,
  unregister: false,
};

export const HISTORY: BootstrapHistoryViewProps = {
  runs: [BOOTSTRAP_RUN, FAILED_RUN],
  loading: false,
};

export const PLAN: BootstrapPlanViewProps = {
  loading: false,
  installing: false,
  onInstall: noop,
  plan: {
    clusterId: CLUSTER.id,
    providerPluginSlug: "aws",
    components: [
      {
        key: "cert_manager",
        title: "cert-manager",
        defaultEnabled: true,
        installedByRecipe: true,
        runningOutsideRecipe: false,
        rationale: "Issues TLS certificates for every managed subdomain.",
        requires: [],
        options: [
          {
            key: "issuer",
            label: "Default issuer",
            default: "letsencrypt-prod",
            choices: [
              { value: "letsencrypt-prod", label: "Let's Encrypt (production)" },
              { value: "letsencrypt-staging", label: "Let's Encrypt (staging)" },
            ],
          },
        ],
        helmValues: {},
      },
      {
        key: "aws_lb_controller",
        title: "AWS Load Balancer Controller",
        defaultEnabled: true,
        installedByRecipe: false,
        runningOutsideRecipe: true,
        rationale: "Provisions ALBs for Ingresses with ingressClass = alb.",
        requires: ["cert_manager"],
        options: [],
        helmValues: {},
      },
      {
        key: "external_dns",
        title: "external-dns",
        defaultEnabled: false,
        installedByRecipe: false,
        runningOutsideRecipe: false,
        rationale: "Writes Route 53 records for app hostnames.",
        requires: [],
        options: [],
        helmValues: {},
      },
    ],
  },
};

export const AGENT: ClusterAgentViewProps = {
  clusterId: CLUSTER.id,
  provisioned: true,
  heartbeatIntervalSeconds: 30,
  issued: null,
  issuing: false,
  deploying: false,
  onIssue: noop,
  onDeploy: noop,
  onDismissIssued: () => {},
};

export const AGENT_ISSUED: ClusterAgentViewProps = {
  ...AGENT,
  issued: {
    clusterId: CLUSTER.id,
    agentKey: "astk_7Qm2vX9pL4rT8nB1cZ6wY3jH5gF0dS",
    intervalSeconds: 30,
    heartbeatUrl: `https://astrolift.example.com/api/clusters/v1/${CLUSTER.id}/heartbeat/`,
    rotated: false,
  },
};

export const INGRESS_AUTH: IngressAuthViewProps = {
  sourceKey: "FIXTURE_CLUSTER_SOURCE",
  poolsError: null,
  onRetryPools: noop,
  clientsError: null,
  onRetryClients: noop,
  providerPluginSlug: "aws",
  ingressClass: "alb",
  existing: CLUSTER.albAuthConfig as IngressAuthViewProps["existing"],
  isAws: true,
  editing: false,
  poolId: "us-west-2_AbCdEf123",
  onPoolIdChange: () => {},
  pools: [
    {
      poolId: "us-west-2_AbCdEf123",
      poolArn: "arn:aws:cognito-idp:us-west-2:123456789012:userpool/us-west-2_AbCdEf123",
      name: "prod-west-apps",
      domain: "prod-west-apps",
      region: "us-west-2",
    },
  ],
  poolsLoading: false,
  poolsErrored: false,
  clients: [{ clientId: "4f8k2m9q1r7t3v5x6z0b", clientName: "astrolift-alb" }],
  clientsLoading: false,
  busy: false,
  reconciling: false,
  onOpenForm: () => {},
  onCancelEdit: () => {},
  onDisable: noop,
  onApply: noop,
  onSaveAndApply: noop,
};

export const CENTRAL_AUTH: CentralAuthViewProps = {
  clusterId: CLUSTER.id,
  view: CLUSTER.oidcAuthConfig as CentralAuthViewProps["view"],
  saving: false,
  onSave: yes,
};

export const INGRESS_CLASS: IngressClassViewProps = {
  clusterId: CLUSTER.id,
  saving: false,
  ingressClass: "alb",
  albGate: true,
  centralAuthConfigured: true,
  onApply: noop,
};

const user = (
  username: string,
  email: string,
  patch: Partial<AstroliftClusterAuthUser> = {}
): AstroliftClusterAuthUser => ({
  username,
  providerUserId: `subject-${username}`,
  email,
  enabled: true,
  status: "CONFIRMED",
  createdAt: "2026-09-20T12:00:00Z",
  groups: [],
  ...patch,
});

export const AUTH_USERS: AuthUsersViewProps = {
  sourceKey: "FIXTURE_AUTH_USERS_SOURCE",
  reviewedSource: true,
  error: null,
  onRetry: noop,
  loading: false,
  view: {
    supported: true,
    reason: "",
    provider: "Amazon Cognito",
    source: {
      providerPluginId: "00000000-0000-0000-0000-000000000001",
      providerPoolId: "us-west-2_fixture",
      sourceVersion: "fixture-version",
    },
    reachNote:
      "A user of this pool can sign in to every app on the cluster that has no access rule of its own.",
    groups: ["platform", "finance"],
    users: [
      user("u1", "ada@example.com", { groups: ["platform"] }),
      user("u2", "grace@example.com", { enabled: false, status: "FORCE_CHANGE_PASSWORD" }),
    ],
  },
  onSetGroups: yes,
  onCreateGroup: yes,
  onToggleEnabled: noop,
  onCreate: yes,
  onSetPassword: yes,
  onResetPassword: yes,
  onDelete: noop,
};
