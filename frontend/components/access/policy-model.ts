/**
 * ABAC policies as sentences (design 3.6). Pure: parses the `Policy` row's
 * JSON (`resource_pattern`, `conditions`, `actor_pattern`) into typed parts,
 * says them in words, and writes them back.
 *
 * The shapes are spec 03 §5's; the backend stores them and (2026-09-28) has
 * no evaluator, so these are the documented shapes, not enforced ones. A
 * condition of a kind this file does not know is kept verbatim, shown as a
 * custom condition, and written back untouched.
 *
 * How a DENY reads: every spec 03 condition is a requirement (a working-hours
 * window, an IP allowlist, approvers, a fresh session), so a DENY policy
 * denies the action *unless* its conditions hold, and an ALLOW policy allows
 * it *only when* they hold. The evaluator, when it lands, must agree.
 */

import type { ScopeKind } from "@/graphql/identity/identity.types";

export type PolicyEffect = "ALLOW" | "DENY";

export const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Weekday = (typeof WEEKDAYS)[number];

export type PolicyCondition =
  | { kind: "time_window"; days: string[]; hours: string[]; tz: string }
  | { kind: "ip_allowlist"; cidrs: string[] }
  | { kind: "approval_required"; min_approvers: number }
  | { kind: "env_match"; env_in: string[] }
  | { kind: "device_assertion"; required_factors: string[] }
  | { kind: "freshness"; max_session_age_minutes: number }
  | { kind: "custom"; raw: unknown };

export type ConditionKind = Exclude<PolicyCondition["kind"], "custom">;

export const CONDITION_KINDS: ReadonlyArray<{ kind: ConditionKind; label: string }> = [
  { kind: "time_window", label: "a time window" },
  { kind: "ip_allowlist", label: "an IP allowlist" },
  { kind: "approval_required", label: "approvers" },
  { kind: "env_match", label: "an environment" },
  { kind: "device_assertion", label: "a sign-in factor" },
  { kind: "freshness", label: "a fresh session" },
];

/** The resource keys spec 03 §5 matches on. */
export const RESOURCE_KEYS = ["app_slug", "project_slug", "env", "region"] as const;
export type ResourceKey = (typeof RESOURCE_KEYS)[number];

export const RESOURCE_KEY_LABEL: Record<ResourceKey, string> = {
  app_slug: "app",
  project_slug: "project",
  env: "environment",
  region: "region",
};

export interface PolicyShape {
  effect: PolicyEffect;
  actionPattern: string;
  /** Resource key to one value or several (globs allowed). */
  resource: Partial<Record<ResourceKey, string[]>>;
  conditions: PolicyCondition[];
  actor: { groups: string[]; role: string };
  scopeLevel?: ScopeKind;
}

// ---------------------------------------------------------------------------
// Parse and serialize
// ---------------------------------------------------------------------------

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const strings = (v: unknown): string[] =>
  Array.isArray(v)
    ? v.filter((x): x is string => typeof x === "string")
    : typeof v === "string" && v
      ? [v]
      : [];

const count = (v: unknown, fallback: number): number =>
  typeof v === "number" && Number.isFinite(v) ? v : fallback;

export function parseCondition(raw: unknown): PolicyCondition {
  if (!isRecord(raw)) return { kind: "custom", raw };
  switch (raw.kind) {
    case "time_window":
      return {
        kind: "time_window",
        days: strings(raw.days),
        hours: strings(raw.hours),
        tz: typeof raw.tz === "string" ? raw.tz : "UTC",
      };
    case "ip_allowlist":
      return { kind: "ip_allowlist", cidrs: strings(raw.cidrs) };
    case "approval_required":
      return { kind: "approval_required", min_approvers: count(raw.min_approvers, 1) };
    case "env_match":
      return { kind: "env_match", env_in: strings(raw.env_in) };
    case "device_assertion":
      return { kind: "device_assertion", required_factors: strings(raw.required_factors) };
    case "freshness":
      return {
        kind: "freshness",
        max_session_age_minutes: count(raw.max_session_age_minutes, 15),
      };
    default:
      return { kind: "custom", raw };
  }
}

export function serializeCondition(c: PolicyCondition): unknown {
  return c.kind === "custom" ? c.raw : c;
}

/** The fields of an `AstroliftPolicy` this module reads. */
export interface PolicyRow {
  effect: string;
  actionPattern: string;
  resourcePattern: unknown;
  conditions: unknown;
  actorPattern: unknown;
  scopeLevel?: ScopeKind;
}

export function parsePolicy(row: PolicyRow): PolicyShape {
  const resource: PolicyShape["resource"] = {};
  if (isRecord(row.resourcePattern)) {
    for (const key of RESOURCE_KEYS) {
      const values = strings(row.resourcePattern[key]);
      if (values.length) resource[key] = values;
    }
  }
  const actor = isRecord(row.actorPattern) ? row.actorPattern : {};
  return {
    effect: row.effect === "ALLOW" ? "ALLOW" : "DENY",
    actionPattern: row.actionPattern || "*",
    resource,
    conditions: Array.isArray(row.conditions) ? row.conditions.map(parseCondition) : [],
    actor: {
      groups: strings(actor.user_in_groups),
      role: typeof actor.user_role_at_scope === "string" ? actor.user_role_at_scope : "",
    },
    scopeLevel: row.scopeLevel,
  };
}

/** The JSON halves of a create/update input. One value stays a string, several a list. */
export function serializePolicy(shape: PolicyShape): {
  effect: PolicyEffect;
  actionPattern: string;
  resourcePattern: Record<string, string | string[]>;
  conditions: unknown[];
  actorPattern: Record<string, unknown>;
} {
  const resourcePattern: Record<string, string | string[]> = {};
  for (const key of RESOURCE_KEYS) {
    const values = shape.resource[key]?.filter(Boolean) ?? [];
    if (values.length) resourcePattern[key] = values.length === 1 ? values[0] : values;
  }
  const actorPattern: Record<string, unknown> = {};
  if (shape.actor.groups.length) actorPattern.user_in_groups = shape.actor.groups;
  if (shape.actor.role) actorPattern.user_role_at_scope = shape.actor.role;
  return {
    effect: shape.effect,
    actionPattern: shape.actionPattern.trim() || "*",
    resourcePattern,
    conditions: shape.conditions.map(serializeCondition),
    actorPattern,
  };
}

export function newCondition(kind: ConditionKind): PolicyCondition {
  switch (kind) {
    case "time_window":
      return {
        kind,
        days: ["mon", "tue", "wed", "thu", "fri"],
        hours: ["09:00-18:00"],
        tz: "UTC",
      };
    case "ip_allowlist":
      return { kind, cidrs: [] };
    case "approval_required":
      return { kind, min_approvers: 2 };
    case "env_match":
      return { kind, env_in: [] };
    case "device_assertion":
      return { kind, required_factors: ["webauthn"] };
    case "freshness":
      return { kind, max_session_age_minutes: 15 };
  }
}

// ---------------------------------------------------------------------------
// Validation, beside each field (spec 44 §5.4)
// ---------------------------------------------------------------------------

const HOURS = /^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-4]):[0-5]\d$/;
const CIDR = /^(\d{1,3}\.){3}\d{1,3}\/(\d|[12]\d|3[0-2])$|^[0-9a-f:]+\/\d{1,3}$/i;

export function conditionError(c: PolicyCondition): string | null {
  switch (c.kind) {
    case "time_window":
      if (c.days.length === 0) return "Pick at least one day.";
      if (c.hours.length === 0) return "Add at least one range, like 09:00-18:00.";
      if (c.hours.some((h) => !HOURS.test(h))) return "Hours are HH:MM-HH:MM, like 09:00-18:00.";
      if (!c.tz.trim()) return "Name a time zone, like America/Los_Angeles.";
      return null;
    case "ip_allowlist":
      if (c.cidrs.length === 0) return "Add at least one CIDR, like 10.0.0.0/8.";
      if (c.cidrs.some((x) => !CIDR.test(x))) return "Each entry is a CIDR, like 10.0.0.0/8.";
      return null;
    case "approval_required":
      return Number.isInteger(c.min_approvers) && c.min_approvers >= 1
        ? null
        : "At least one approver.";
    case "env_match":
      return c.env_in.length ? null : "Name at least one environment.";
    case "device_assertion":
      return c.required_factors.length ? null : "Pick at least one factor.";
    case "freshness":
      return Number.isInteger(c.max_session_age_minutes) && c.max_session_age_minutes >= 1
        ? null
        : "At least one minute.";
    case "custom":
      return null;
  }
}

// ---------------------------------------------------------------------------
// In words
// ---------------------------------------------------------------------------

/** A run of the sentence: plain words, or a value shown in mono. */
export interface Segment {
  text: string;
  value?: boolean;
}

const w = (text: string): Segment => ({ text });
const v = (text: string): Segment => ({ text, value: true });

/** "a, b or c", each a value. */
function list(values: string[], joiner = "or"): Segment[] {
  const out: Segment[] = [];
  values.forEach((x, i) => {
    if (i > 0) out.push(w(i === values.length - 1 ? ` ${joiner} ` : ", "));
    out.push(v(x));
  });
  return out;
}

const DAY_LABEL: Record<string, string> = {
  mon: "Mon",
  tue: "Tue",
  wed: "Wed",
  thu: "Thu",
  fri: "Fri",
  sat: "Sat",
  sun: "Sun",
};

/** Consecutive weekdays collapse: mon..fri reads "Mon to Fri". */
export function describeDays(days: string[]): string {
  const idx = days
    .map((d) => WEEKDAYS.indexOf(d as Weekday))
    .filter((i) => i >= 0)
    .sort((a, b) => a - b);
  if (idx.length === 7) return "every day";
  const contiguous = idx.length > 2 && idx.every((d, i) => i === 0 || d === idx[i - 1] + 1);
  if (contiguous) return `${DAY_LABEL[WEEKDAYS[idx[0]]]} to ${DAY_LABEL[WEEKDAYS[idx.at(-1)!]]}`;
  return idx.map((i) => DAY_LABEL[WEEKDAYS[i]]).join(", ") || "no day";
}

export function conditionSegments(c: PolicyCondition): Segment[] {
  switch (c.kind) {
    case "time_window":
      return [
        w("it is "),
        v(describeDays(c.days)),
        w(" "),
        ...list(c.hours.map((h) => h.replace("-", " to "))),
        w(" "),
        v(c.tz),
      ];
    case "ip_allowlist":
      return [w("the request comes from "), ...list(c.cidrs.length ? c.cidrs : ["no range"])];
    case "approval_required":
      return [
        v(String(c.min_approvers)),
        w(c.min_approvers === 1 ? " approver approves" : " approvers approve"),
      ];
    case "env_match":
      return [w("the environment is "), ...list(c.env_in.length ? c.env_in : ["none"])];
    case "device_assertion":
      return [
        w("the session used "),
        ...list(c.required_factors.length ? c.required_factors : ["none"], "and"),
      ];
    case "freshness":
      return [w("the person signed in within "), v(`${c.max_session_age_minutes} min`)];
    case "custom":
      return [
        w("custom condition "),
        v(isRecord(c.raw) && typeof c.raw.kind === "string" ? c.raw.kind : JSON.stringify(c.raw)),
      ];
  }
}

export function actionSegments(pattern: string): Segment[] {
  const p = pattern.trim() || "*";
  return p === "*" ? [w("every action")] : [v(p)];
}

export function resourceSegments(resource: PolicyShape["resource"]): Segment[] {
  const out: Segment[] = [];
  const app = resource.app_slug ?? [];
  const project = resource.project_slug ?? [];
  out.push(w(app.length ? "app " : "anything"));
  if (app.length) out.push(...list(app));
  if (project.length) out.push(w(" in project "), ...list(project));
  if (resource.env?.length) out.push(w(" in "), ...list(resource.env));
  if (resource.region?.length) out.push(w(" in region "), ...list(resource.region));
  return out;
}

export function actorSegments(actor: PolicyShape["actor"]): Segment[] {
  if (actor.groups.length && actor.role) {
    return [w("members of "), ...list(actor.groups), w(" holding "), v(actor.role)];
  }
  if (actor.groups.length) return [w("members of "), ...list(actor.groups)];
  if (actor.role) return [w("holders of "), v(actor.role)];
  return [w("everyone")];
}

/**
 * The whole policy: "Deny app.deploy on anything in production for everyone
 * unless it is Mon to Fri 09:00 to 18:00 America/Los_Angeles."
 */
export function policySegments(shape: PolicyShape): Segment[] {
  const out: Segment[] = [
    w(shape.effect === "DENY" ? "Deny " : "Allow "),
    ...actionSegments(shape.actionPattern),
    w(" on "),
    ...resourceSegments(shape.resource),
    w(" for "),
    ...actorSegments(shape.actor),
  ];
  shape.conditions.forEach((c, i) => {
    out.push(w(i === 0 ? (shape.effect === "DENY" ? " unless " : " only when ") : " and "));
    out.push(...conditionSegments(c));
  });
  out.push(w("."));
  return out;
}

export function policySentence(shape: PolicyShape): string {
  return policySegments(shape)
    .map((s) => s.text)
    .join("");
}
