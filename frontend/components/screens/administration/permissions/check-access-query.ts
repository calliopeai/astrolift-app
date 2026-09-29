/**
 * Check access's question in the URL (design 3.7), so an answer can be
 * linked and any page can open it preselected. Pure.
 *
 *   who      the user id asked about; absent means the viewer
 *   can      the permission slug
 *   on       the scope, `<kind>:<id>` with the kind in lower case, as the
 *            Grant access page takes its `scope` (access-nav.ts `grantHref`)
 *   compare  present, even empty, for the compare view; its value is the
 *            second person
 */

import type { ScopeKind, ScopeRef } from "@/components/access/access-model";

/**
 * The page's route. It is the Diagnostics route Check access replaces, as
 * the Access switcher links it (access-nav.ts), until the page moves to
 * `/administration/access/check`.
 */
export const CHECK_ACCESS_PATH = "/administration/permissions/diagnostics";

const KINDS: readonly ScopeKind[] = ["ORG", "TEAM", "PROJECT", "APP"];

export interface CheckAccessQuery {
  who: string | null;
  can: string | null;
  on: Pick<ScopeRef, "kind" | "id"> | null;
  compare: string | null;
}

export function formatScopeParam(scope: Pick<ScopeRef, "kind" | "id">): string {
  return `${scope.kind.toLowerCase()}:${scope.id}`;
}

/** `app:<id>` back to a kind and id; null for anything else. */
export function parseScopeParam(
  raw: string | null | undefined
): { kind: ScopeKind; id: string } | null {
  if (!raw) return null;
  const colon = raw.indexOf(":");
  if (colon <= 0) return null;
  const kind = raw.slice(0, colon).toUpperCase() as ScopeKind;
  const id = raw.slice(colon + 1);
  return KINDS.includes(kind) && id ? { kind, id } : null;
}

export function checkAccessHref(q: Partial<CheckAccessQuery>): string {
  const params = new URLSearchParams();
  if (q.who) params.set("who", q.who);
  if (q.can) params.set("can", q.can);
  if (q.on) params.set("on", formatScopeParam(q.on));
  if (q.compare !== undefined && q.compare !== null) params.set("compare", q.compare);
  const qs = params.toString();
  return qs ? `${CHECK_ACCESS_PATH}?${qs}` : CHECK_ACCESS_PATH;
}

export function parseCheckAccessQuery(params: URLSearchParams): CheckAccessQuery {
  return {
    who: params.get("who") || null,
    can: params.get("can") || null,
    on: parseScopeParam(params.get("on")),
    compare: params.get("compare"),
  };
}
