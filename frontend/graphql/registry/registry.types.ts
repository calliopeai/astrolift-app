/**
 * Registry types — facade over the codegen output.
 */

import type {
  AstroliftAppConfigDrift as GeneratedAppConfigDrift,
  AstroliftAppDeploymentSummary as GeneratedAppDeploymentSummary,
  AstroliftAppHealthPulse as GeneratedAppHealthPulse,
  AstroliftAppReprovisionState as GeneratedAppReprovisionState,
  AstroliftAppSettingsLastModified as GeneratedAppSettingsLastModified,
  AstroliftAppTeamAccess as GeneratedAppTeamAccess,
  AstroliftContainer as GeneratedContainer,
  AstroliftRegisteredApp as GeneratedRegisteredApp,
  AstroliftWorkload as GeneratedWorkload,
} from "@/graphql/__generated__/schema";

export type AstroliftGuid = string;

export type ProvisioningStatus = "pending" | "provisioning" | "ready" | "failed";

export type SourceKind = "github" | "gitlab" | "bitbucket" | "gitea" | "git_url";

export type TriggerMode = "auto_on_push" | "manual" | "external_ci" | "cron";

export type WorkloadKind = "deployment" | "statefulset" | "job" | "cronjob";

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
};

export type AstroliftContainer = Omit<GeneratedContainer, "healthcheckKind"> & {
  healthcheckKind: HealthcheckKind;
};

export type AppTeamAccessLevel = "viewer" | "deployer" | "owner";

export type AstroliftAppTeamAccess = Omit<GeneratedAppTeamAccess, "accessLevel"> & {
  accessLevel: AppTeamAccessLevel;
};
