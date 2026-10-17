/**
 * The semantic layer detail's shared shapes and rules (#2427): the layer the
 * frame reads once and hands to every tab, what the viewer may do to it, and
 * what each action-safety gate asks. Pure.
 */
import type { GetBrainQuery } from "@/graphql/__generated__/operations";

export type FramedBrain = NonNullable<GetBrainQuery["astroliftBrain"]>;
export type BrainSubstrate = NonNullable<FramedBrain["substrate"]>;

/** What the viewer may do to this layer: the server checks each again. */
export interface BrainCan {
  /** `app.update`: share and unshare. */
  share: boolean;
  /** Sharing as editor (deployer) also needs `app.deploy`. */
  shareAsEditor: boolean;
  /** `app.deploy`: rebuild, with or without ingest. */
  rebuild: boolean;
  /** `app.update` and `app.deploy`: the graduation commit deploys. */
  graduate: boolean;
  /** `app.delete`: archive. */
  archive: boolean;
  /** `app.update`: restoreApp on the archived app. */
  restore: boolean;
  /** `app.create` somewhere: Host an app (the dialog picks the project). */
  host: boolean;
  /** `audit_log.read`: the access history. */
  readAudit: boolean;
}

export const NOTHING: BrainCan = {
  share: false,
  shareAsEditor: false,
  rebuild: false,
  graduate: false,
  archive: false,
  restore: false,
  host: false,
  readAudit: false,
};

export const EVERYTHING: BrainCan = {
  share: true,
  shareAsEditor: true,
  rebuild: true,
  graduate: true,
  archive: true,
  restore: true,
  host: true,
  readAudit: true,
};

/** What an action-safety gate asks before the action runs (#2202). */
export interface GateAsk {
  confirmation: boolean;
  verification: boolean;
  approval: boolean;
  minimumApprovals: number;
}

/** Gates are on by default: confirmation and a fresh sign-in, no approval. */
export const DEFAULT_ASK: GateAsk = {
  confirmation: true,
  verification: true,
  approval: false,
  minimumApprovals: 0,
};

/** The ask for one action from `astroliftActionSafety(appSlug)`, or the default. */
export function gateAsk(
  rows:
    | readonly {
        action: string;
        enabled: boolean;
        confirmation: boolean;
        verification: boolean;
        approval: boolean;
        minimumApprovals: number;
      }[]
    | null
    | undefined,
  action: "brain.share" | "brain.delete"
): GateAsk {
  const row = rows?.find((r) => r.action === action);
  if (!row) return DEFAULT_ASK;
  if (!row.enabled)
    return { confirmation: false, verification: false, approval: false, minimumApprovals: 0 };
  return {
    confirmation: row.confirmation,
    verification: row.verification,
    approval: row.approval,
    minimumApprovals: row.minimumApprovals,
  };
}

/** `viewer` and `deployer` are what shareBrain grants; `owner` is managed on the app. */
export const SHARE_LEVELS = ["viewer", "deployer"] as const;
export type ShareLevel = (typeof SHARE_LEVELS)[number];

export function shareLevel(access: string): ShareLevel | "owner" | null {
  if (access === "owner") return "owner";
  return (SHARE_LEVELS as readonly string[]).includes(access) ? (access as ShareLevel) : null;
}

/** One row per hosted app, its environments together. */
export function hostedAppRows(
  hosted: readonly { slug: string; environment: string }[]
): { slug: string; environments: string[] }[] {
  const bySlug = new Map<string, string[]>();
  for (const row of hosted) {
    const envs = bySlug.get(row.slug) ?? [];
    if (row.environment && !envs.includes(row.environment)) envs.push(row.environment);
    bySlug.set(row.slug, envs);
  }
  return [...bySlug.entries()]
    .map(([slug, environments]) => ({ slug, environments: environments.sort() }))
    .sort((a, b) => a.slug.localeCompare(b.slug));
}

/** The environment names a rebuild or status can name: the served ones, or production. */
export function environmentNames(brain: Pick<FramedBrain, "environments">): string[] {
  const names = brain.environments.map((e) => e.name);
  return names.length ? names : ["production"];
}

/** The environment a tab reads (`?env=`), when the layer has it; else the default one. */
export function pickEnvironment(
  brain: Pick<FramedBrain, "environments">,
  asked: string | null
): string {
  const names = environmentNames(brain);
  if (asked && names.includes(asked)) return asked;
  return names.includes("production") ? "production" : names[0]!;
}

/** The archived app behind a slug, when it is a semantic layer (`[app] kind = "mimwell"`). */
export const BRAIN_KINDS: readonly string[] = ["mimwell"];

/** The audit actions a layer's access history shows. */
export const SHARE_ACTIONS = ["brain.share", "brain.unshare"] as const;

/** `astroliftAuditEventsPage(filter:)` for one layer's share history. */
export function shareHistoryFilter(slug: string) {
  return {
    action: [...SHARE_ACTIONS],
    actor: null,
    decision: null,
    since: null,
    subjectUser: null,
    // brain.share and brain.unshare record the layer by app slug.
    targetId: slug,
    targetKind: ["RegisteredApp"],
    until: null,
  };
}

export interface HistoryEntry {
  id: string;
  at: string;
  actor: string;
  kind: "share" | "unshare" | "other";
  action: string;
  /** ok: it changed access; refused: a gate or a permission stopped it; failed: it was checked and did not apply. */
  outcome: "ok" | "refused" | "failed";
  /** The server's error code, when it did not apply. */
  code: string | null;
  /** Who the layer was shared with after the change, as the audit row recorded it. */
  sharedWith: { team: string; access: string }[] | null;
  /** `mcp` when an agent's token made the call. */
  surface: string | null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** One audit row as the history reads it; `data` is the row's JSON, narrowed here. */
export function historyEntry(event: {
  id: string;
  occurredAt: string;
  actorDisplay: string;
  actorId: string;
  action: string;
  decision: string;
  data: unknown;
}): HistoryEntry {
  const data = record(event.data);
  const code = typeof data.error_code === "string" && data.error_code ? data.error_code : null;
  const shared = Array.isArray(data.shared_with)
    ? data.shared_with
        .map(record)
        .filter((s) => typeof s.team === "string" && typeof s.access === "string")
        .map((s) => ({ team: s.team as string, access: s.access as string }))
    : null;
  return {
    id: event.id,
    at: event.occurredAt,
    actor: event.actorDisplay || event.actorId,
    kind:
      event.action === "brain.share"
        ? "share"
        : event.action === "brain.unshare"
          ? "unshare"
          : "other",
    action: event.action,
    outcome: event.decision === "DENY" ? "refused" : code ? "failed" : "ok",
    code,
    sharedWith: shared,
    surface: typeof data.surface === "string" && data.surface ? data.surface : null,
  };
}
