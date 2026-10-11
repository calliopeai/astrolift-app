import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import type {
  CognitoUserPoolClientsQuery,
  CognitoUserPoolsQuery,
} from "@/graphql/__generated__/operations";
import type { ClusterHeartbeatFields } from "@/lib/cluster-heartbeat";

// The schema types heartbeatStatus as a plain String; narrow it to the four
// values the backend emits (#808).
export type ClusterWithHeartbeat = AstroliftTenantCluster &
  Pick<ClusterHeartbeatFields, "heartbeatStatus">;

export type Lifecycle = NonNullable<ClusterWithHeartbeat["lifecycle"]>;

/** What the viewer may change on the settings tab, one flag per permission. */
export interface ClusterSettingsAccess {
  /** `cluster.manage`: lifecycle actions, the keep-alive agent, the bootstrap recipe. */
  manage: boolean;
  /** `cluster.update`: ingress class, central auth, ingress auth. */
  update: boolean;
  /** `cluster.users`: the central auth's sign-in users. */
  users: boolean;
  /** `cluster.unregister`: decommission. */
  unregister: boolean;
}

// ─── Bootstrap plan (#67 + #66) ─────────────────────────────────────────

export interface BootstrapOptionChoice {
  value: string;
  label: string;
}

export interface BootstrapOption {
  key: string;
  label: string;
  default: string;
  choices: BootstrapOptionChoice[];
}

export interface BootstrapComponent {
  key: string;
  title: string;
  defaultEnabled: boolean;
  installedByRecipe: boolean;
  runningOutsideRecipe: boolean;
  /** Why the install refuses it (calliope-installer#447); offered disabled. */
  withheldReason?: string | null;
  rationale: string;
  requires: string[];
  options: BootstrapOption[];
  helmValues: Record<string, unknown>;
}

export interface BootstrapPlan {
  clusterId: string;
  providerPluginSlug: string;
  components: BootstrapComponent[];
}

/**
 * What the recipe card checks before the operator touches it (#2119).
 *
 * A component the recipe installed stays checked: an install deletes the
 * release of everything it is not given. One the probe found running outside
 * the recipe (a controller installed by hand) is never pre-checked, or
 * accepting the defaults installs a second copy that fights the first.
 */
export function preselected(c: {
  defaultEnabled: boolean;
  installedByRecipe: boolean;
  runningOutsideRecipe: boolean;
  withheldReason?: string | null;
}): boolean {
  if (c.withheldReason) return false;
  if (c.installedByRecipe) return true;
  return c.defaultEnabled && !c.runningOutsideRecipe;
}

// ─── Last bootstrap (#319) ──────────────────────────────────────────────

export interface BootstrapRun {
  id: string;
  status: string;
  chartVersion: string;
  installedReleases: unknown;
  cliVersion: string;
  errorMessage: string;
  startedAt: string;
  endedAt: string;
  triggeredByUsername?: string | null;
}

export function bootstrapReleaseCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

// ─── Keep-alive agent (#808) ────────────────────────────────────────────

export interface AgentKeyIssuedData {
  clusterId: string;
  agentKey: string;
  intervalSeconds: number;
  heartbeatUrl: string;
  rotated: boolean;
}

// ─── Ingress auth (#851) ────────────────────────────────────────────────

export interface IngressAuthConfig {
  user_pool_arn: string;
  user_pool_client_id: string;
  user_pool_domain: string;
}

export function isAlbAuthConfig(v: unknown): v is IngressAuthConfig {
  return (
    typeof v === "object" &&
    v !== null &&
    "user_pool_arn" in v &&
    "user_pool_client_id" in v &&
    "user_pool_domain" in v
  );
}

export type CognitoUserPool = CognitoUserPoolsQuery["astroliftCognitoUserPools"][number];
export type CognitoUserPoolClient =
  CognitoUserPoolClientsQuery["astroliftCognitoUserPoolClients"][number];

// Parse the pool id out of a Cognito user-pool ARN
// (arn:aws:cognito-idp:<region>:<account>:userpool/<poolId>). Returns ""
// when the string isn't a recognizable pool ARN — used so the dependent
// app-client picker can still query when editing an already-saved or
// pasted ARN, without forcing the operator to re-pick the pool.
export function poolIdFromArn(arn: string): string {
  const m = arn.match(/userpool\/(.+)$/);
  return m ? m[1] : "";
}

// ─── Central auth (#2119) ───────────────────────────────────────────────

/** The redacted view the server returns (``redact_oidc_auth_config``). */
export interface OidcView {
  discovery_url?: string;
  client_id?: string;
  auth_proxy_host?: string;
  jwks_uri?: string;
  logout_url?: string;
  upstream_connector?: string;
  client_secret_set?: boolean;
  cookie_secret_set?: boolean;
  gateway_secret_set?: boolean;
}

export function oidcView(cluster: AstroliftTenantCluster): OidcView | null {
  const v = (cluster as { oidcAuthConfig?: unknown }).oidcAuthConfig;
  return v && typeof v === "object" ? (v as OidcView) : null;
}

/** The three routing keys every edge gates on (``edge_configured``). */
export function oidcComplete(v: OidcView | null): boolean {
  return Boolean(v?.discovery_url && v?.client_id && v?.auth_proxy_host);
}

/** The fields the central-auth form edits. */
export interface CentralAuthDraft {
  discoveryUrl: string;
  clientId: string;
  authHost: string;
  jwksUri: string;
  clientSecret: string;
}

// ─── Sign-in users (#2131) ──────────────────────────────────────────────

export interface NewAuthUser {
  email: string;
  password: string | null;
  permanent: boolean;
  groups: string[];
}
