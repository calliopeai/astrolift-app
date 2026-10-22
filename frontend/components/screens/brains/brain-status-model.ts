/**
 * What a semantic layer's status says, read the same way on the list, the
 * overview and the substrate tab (#2427). Pure: the API's values in, the
 * tone and the facts each surface shows out. Unknown values stay raw, so a
 * status this build does not know reads as itself and never as a message
 * key.
 */
import type {
  BrainStatusFieldsFragment,
  BrainSummaryFieldsFragment,
} from "@/graphql/__generated__/operations";
import type { StatusTone } from "@/lib/status-tones";

export type BrainStatus = BrainStatusFieldsFragment;
/** The cached summary a list row carries (#2444): no host reads. */
export type BrainSummary = BrainSummaryFieldsFragment;

/** One status read: in flight, refused or failed, or the answer (null: no such environment). */
export type BrainStatusResult =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; status: BrainStatus | null };

/** Deployment states DeploymentStatusPill knows (`apps.deployments.statuses`). */
export const DEPLOYMENT_STATUSES = [
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
  "running",
  "failed",
  "superseded",
  "rolled_back",
] as const;
export type KnownDeploymentStatus = (typeof DEPLOYMENT_STATUSES)[number];

export function knownDeploymentStatus(status: string): KnownDeploymentStatus | null {
  return (DEPLOYMENT_STATUSES as readonly string[]).includes(status)
    ? (status as KnownDeploymentStatus)
    : null;
}

/**
 * The environment a layer's status is read in: production when it has one
 * (or nothing is served yet, so production is where a first deploy lands),
 * else the first served environment.
 */
export function statusEnvironment(environments: readonly { name: string }[]): string {
  if (environments.length === 0 || environments.some((e) => e.name === "production"))
    return "production";
  return environments[0]!.name;
}

export type Health =
  | { kind: "noEnvironment" }
  | { kind: "notDeployed" }
  | { kind: "deployment"; status: KnownDeploymentStatus }
  | { kind: "other"; status: string };

/** The latest deployment's state in the environment, or why there is none. */
export function healthOf(status: Pick<BrainStatus, "deployment"> | null): Health {
  if (!status) return { kind: "noEnvironment" };
  const deployment = status.deployment;
  if (!deployment) return { kind: "notDeployed" };
  const known = knownDeploymentStatus(deployment.status);
  return known
    ? { kind: "deployment", status: known }
    : { kind: "other", status: deployment.status };
}

export type IngestState = "running" | "succeeded" | "failed" | "other";

/**
 * A knowledge-refresh run as the host reports it: `status` is queued,
 * in_progress or completed, `conclusion` says how a completed run ended.
 */
export function ingestState(run: { status: string; conclusion: string }): IngestState {
  if (run.status !== "completed") return "running";
  if (run.conclusion === "success") return "succeeded";
  if (["failure", "timed_out", "startup_failure"].includes(run.conclusion)) return "failed";
  return "other";
}

export const INGEST_TONE: Record<IngestState, StatusTone> = {
  running: "info",
  succeeded: "ok",
  failed: "error",
  other: "muted",
};

export interface Freshness {
  /** `app/brain-manifest.json` generated at the running commit; null when uncompiled. */
  compiledAt: string | null;
  /** The running commit differs from the branch head; null when unknown. */
  behind: boolean | null;
  ingest: { state: IngestState; at: string; url: string } | null;
  /** Host reads that failed, verbatim. */
  warnings: string[];
}

export function freshnessOf(
  status: Pick<BrainStatus, "compiledAt" | "behind" | "lastIngest"> & {
    warnings?: readonly string[];
  }
): Freshness {
  return {
    compiledAt: status.compiledAt ?? null,
    behind: status.behind ?? null,
    ingest: status.lastIngest
      ? {
          state: ingestState(status.lastIngest),
          at: status.lastIngest.createdAt,
          url: status.lastIngest.url,
        }
      : null,
    warnings: [...(status.warnings ?? [])],
  };
}

/** A commit as the screens print it. */
export function shortSha(sha: string | null | undefined): string {
  return (sha ?? "").slice(0, 7);
}

/** `<url>/mcp`: where a client reaches a layer's MCP surface. */
export function mcpUrl(url: string): string {
  return `${url.replace(/\/+$/, "")}/mcp`;
}

/** Managed-service and volume states as status tones; unknown ones are muted. */
export function resourceTone(state: string): StatusTone {
  switch (state) {
    case "active":
      return "ok";
    case "pending":
    case "provisioning":
    case "updating":
      return "info";
    case "deprovisioning":
    case "unbound":
      return "warn";
    case "failed":
      return "error";
    default:
      return "muted";
  }
}

/** True while the state is moving: its dot pulses (reduced motion holds it still). */
export function resourceInFlight(state: string): boolean {
  return ["pending", "provisioning", "updating", "deprovisioning"].includes(state);
}
