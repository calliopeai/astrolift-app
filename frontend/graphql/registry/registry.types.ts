/**
 * Registry types — facade over the codegen output.
 */

import type {
  AstroliftAppConfigDrift as GeneratedAppConfigDrift,
  AstroliftAppDeploymentSummary as GeneratedAppDeploymentSummary,
  AstroliftAppHealthPulse as GeneratedAppHealthPulse,
  AstroliftAppListStatusFilter as GeneratedAppListStatusFilter,
  AstroliftAppReprovisionState as GeneratedAppReprovisionState,
  AstroliftAppSettingsLastModified as GeneratedAppSettingsLastModified,
  AstroliftAppSourceKindFilter as GeneratedAppSourceKindFilter,
  AstroliftAppTeamAccess as GeneratedAppTeamAccess,
  AstroliftContainer as GeneratedContainer,
  AstroliftRegisteredApp as GeneratedRegisteredApp,
  AstroliftRetentionPolicy as GeneratedRetentionPolicy,
  AstroliftWorkload as GeneratedWorkload,
} from "@/graphql/__generated__/schema";

export type AstroliftRetentionPolicy = Pick<
  GeneratedRetentionPolicy,
  "id" | "signal" | "retentionDays"
>;

export type AstroliftGuid = string;

export type ProvisioningStatus = "pending" | "provisioning" | "ready" | "failed";

export type SourceKind = "github" | "gitlab" | "bitbucket" | "gitea" | "git_url";

export type TriggerMode = "auto_on_push" | "manual" | "external_ci" | "cron";

export type WorkloadKind =
  | "deployment"
  | "statefulset"
  | "job"
  | "cronjob"
  | "task"
  | "agent"
  | "workflow"
  | "function";

export type ManifestSyncState = "in_sync" | "db_ahead" | "repo_ahead" | "diverged";

export type HealthcheckKind = "none" | "http" | "tcp" | "exec";

/**
 * Coarse per-app freshness signal surfaced on the apps list (#405).
 * Mirrors the backend's ``AstroliftAppHealthPulseStatus`` enum —
 * Strawberry sends enums by name, so these are uppercase on the
 * wire (and match the codegen output).
 */
export type AppHealthPulseStatus = "OK" | "DEGRADED" | "STALE" | "NEVER";

export type AstroliftAppHealthPulse = Omit<GeneratedAppHealthPulse, "status"> & {
  status: AppHealthPulseStatus;
};

/**
 * Slim deployment shape carried inline on each app row (#405).
 * Backend keeps ``status`` as a free-form string so the list query
 * doesn't have to import the lifecycle enum; FE narrows it to the
 * deployment-status union it already uses elsewhere.
 */
export type LatestDeploymentStatus =
  | "pending_approval"
  | "pending"
  | "deploying"
  | "running"
  | "redeploying"
  | "failed"
  | "superseded"
  | "rolled_back";

export type AstroliftAppDeploymentSummary = Omit<GeneratedAppDeploymentSummary, "status"> & {
  status: LatestDeploymentStatus;
};

/**
 * Reprovision-callout state surfaced on the app overview (#407 A).
 *
 * ``state`` narrows the wire string to the known states:
 * - ``pending`` / ``provisioning`` / ``failed`` — mirrors the
 *   ``ProvisioningStatus`` triplet that warrants a callout.
 * - ``ready_missing_registry`` — synthetic state for the ready-but-no-
 *   registry-repo-uri recovery gap.
 * - ``""`` — no callout applies (the ready + healthy case).
 *
 * Unknown future states fall through to ``string`` so an FE older than
 * the backend doesn't crash on a new code; it renders the raw reason
 * the resolver computed.
 */
export type ReprovisionStateKey =
  | ""
  | "pending"
  | "provisioning"
  | "failed"
  | "ready_missing_registry";

export type AstroliftAppReprovisionState = Omit<GeneratedAppReprovisionState, "state"> & {
  state: ReprovisionStateKey | string;
};

/**
 * Config-drift rollup surfaced on the app overview (#407 C). Always
 * carries a ``hasDrift`` boolean + a list of field-paths that diverged
 * (``manifest_hash`` / ``image_tag`` / ``repo_unsynced``). FE renders
 * one bullet per entry with copy from i18n.
 */
export type AstroliftAppConfigDrift = GeneratedAppConfigDrift;

/**
 * Per-section "Modified N ago" timestamps for the Settings landing
 * card grid (#454). One field per LINK_SECTIONS card; each is the
 * ``max(updated_at)`` across the section's primary resource scoped
 * to the parent app, or ``null`` when no rows exist (FE hides the
 * caption rather than rendering a misleading default).
 *
 * Populated only on the ``astroliftApp(slug)`` detail resolver;
 * list-shape queries leave the wrapper ``null`` to keep the cheap
 * list path cheap.
 */
export type AstroliftAppSettingsLastModified = GeneratedAppSettingsLastModified;

/**
 * Live progress rollup surfaced on the app overview while the
 * provisioning workflow is running. Populated only when
 * ``provisioningStatus == "provisioning"``; ``null`` otherwise. Manual
 * entry until ``make codegen`` regenerates the generated TypeScript
 * types from the updated schema.graphql.
 *
 * - ``currentStep``: free-form label of the step currently running
 *   (e.g. ``"provisioning:registry+namespace"``).
 * - ``completed``: stable step keys that have finished, in completion
 *   order (e.g. ``["registry"]``).
 * - ``totalSteps``: stable step keys for the full provisioning plan,
 *   in execution order. Drives the step indicator on the FE.
 */
export interface ProvisioningProgress {
  currentStep: string;
  completed: string[];
  totalSteps: string[];
}

export type AstroliftRegisteredApp = Omit<
  GeneratedRegisteredApp,
  | "sourceKind"
  | "provisioningStatus"
  | "triggerMode"
  | "manifestSyncState"
  | "healthPulse"
  | "latestDeployment"
  | "reprovision"
  | "configDrift"
  | "settingsLastModified"
> & {
  sourceKind: SourceKind;
  provisioningStatus: ProvisioningStatus;
  triggerMode: TriggerMode;
  manifestSyncState: ManifestSyncState;
  healthPulse: AstroliftAppHealthPulse | null;
  latestDeployment: AstroliftAppDeploymentSummary | null;
  reprovision: AstroliftAppReprovisionState;
  configDrift: AstroliftAppConfigDrift | null;
  settingsLastModified: AstroliftAppSettingsLastModified | null;
  /**
   * Count of currently-live preview environments for this app
   * (#730). Manual entry until `make codegen` regenerates the
   * generated TypeScript types from the updated schema.graphql.
   */
  activePreviewCount: number;
  /**
   * Live step-by-step provisioning progress. Populated only when
   * ``provisioningStatus == "provisioning"``; ``null`` otherwise.
   * Manual entry until `make codegen` regenerates the generated
   * TypeScript types from the updated schema.graphql.
   */
  provisioningProgress?: ProvisioningProgress | null;
  /**
   * Full platform-managed hostname (e.g. `my-app.astrolift.example.com`).
   * Computed from app.subdomain + ManagedDomain.zone. Empty string when no domain
   * is configured. Manual entry until `make codegen` regenerates types.
   */
  managedHostname: string;
};

export type AstroliftWorkload = Omit<GeneratedWorkload, "kind"> & {
  kind: WorkloadKind;
  /**
   * In-cluster DNS name for the workload's ClusterIP Service —
   * ``<slug>.<namespace>.svc.cluster.local`` (#429). Populated by
   * the backend; empty string when the namespace half can't be
   * computed (e.g. orphan app rows). Codegen will pick this up on
   * the next ``make codegen`` run; the manual entry keeps the
   * workload detail page typesafe until then.
   */
  inClusterServiceFqdn: string;
  /** Volume declarations from the manifest (#739). Each entry mirrors the parsed VolumeDecl dict shape. */
  volumes: Record<string, unknown>[];
};

export type AstroliftContainer = Omit<GeneratedContainer, "healthcheckKind"> & {
  healthcheckKind: HealthcheckKind;
  startupProbe: Record<string, unknown> | null;
  readinessProbe: Record<string, unknown> | null;
  livenessProbe: Record<string, unknown> | null;
};

export type AppTeamAccessLevel = "viewer" | "deployer" | "owner";

export type AstroliftAppTeamAccess = Omit<GeneratedAppTeamAccess, "accessLevel"> & {
  accessLevel: AppTeamAccessLevel;
};

/**
 * Apps-list filter axes (#481). Re-export with the GraphQL-generated
 * shape — Strawberry uppercases enum members on the wire, so these
 * match the codegen output exactly.
 */
export type AppListStatusFilter = GeneratedAppListStatusFilter;
export type AppSourceKindFilter = GeneratedAppSourceKindFilter;

/**
 * Cursor-paginated apps-list slice surfaced by ``LIST_APPS_PAGE`` (#481).
 * ``items`` carries the typed registered apps; ``nextCursor`` is the
 * opaque cursor to feed back through ``fetchMore`` to request the
 * next page (null when the caller has reached the end of the
 * filtered result). ``totalCount`` is the filter-aware total — useful
 * for "Showing N of M" copy on the page header.
 */
export type AstroliftRegisteredAppPage = {
  items: AstroliftRegisteredApp[];
  nextCursor: string | null;
  totalCount: number;
};
