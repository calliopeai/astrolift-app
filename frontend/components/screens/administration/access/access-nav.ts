import type { Crumb } from "@/components/shell/ShellHeader";
import { NAV } from "@/lib/shell/nav-model";

import { adminCrumb } from "./admin-crumbs";

/**
 * Admin › Access (access UX design 3.1): one home for people, teams, roles,
 * policies and checking access. Its second crumb, `Access ▾`, switches
 * between the five functions.
 *
 * People and Teams live under `/administration/access` now. Roles, Policies
 * and Check access still answer at their current routes until their screens
 * move (design 5); the switcher links there so no option is a dead end.
 */
export type AccessFunction = "people" | "teams" | "roles" | "policies" | "check";

export const ACCESS_BASE = "/administration/access";
export const PEOPLE_HREF = `${ACCESS_BASE}/people`;
export const TEAMS_HREF = `${ACCESS_BASE}/teams`;
export const GRANT_HREF = `${ACCESS_BASE}/grant`;

export const ACCESS_FUNCTIONS: ReadonlyArray<{ key: AccessFunction; label: string; href: string }> =
  [
    { key: "people", label: "People", href: PEOPLE_HREF },
    { key: "teams", label: "Teams", href: TEAMS_HREF },
    { key: "roles", label: "Roles", href: "/administration/permissions" },
    { key: "policies", label: "Policies", href: "/administration/policies" },
    { key: "check", label: "Check access", href: "/administration/permissions/diagnostics" },
  ];

/**
 * The rail row the Admin crumb checks: `access` once the nav has the one
 * Access row, `members` until then, so the crumb and the rail agree (spec 44
 * §4.4 rule 7) either way.
 */
function adminKey(): string {
  const hasAccess = NAV.some((a) =>
    a.groups.some((g) => g.functions.some((f) => f.key === "access"))
  );
  return hasAccess ? "access" : "members";
}

/**
 * `Admin ▾ › Access ▾ › People` on a list, `Admin ▾ › Access ▾ › People ›
 * ada` on a detail (at most four crumbs, spec 44 §4.4 rule 2).
 */
export function accessCrumbs(fn: AccessFunction, detail?: string): Crumb[] {
  const current = ACCESS_FUNCTIONS.find((f) => f.key === fn)!;
  const crumbs: Crumb[] = [
    adminCrumb(adminKey()),
    {
      label: "Access",
      switcher: ACCESS_FUNCTIONS.map((f) => ({
        label: f.label,
        href: f.href,
        active: f.key === fn,
      })),
    },
  ];
  if (detail === undefined) {
    crumbs.push({ label: current.label });
  } else {
    crumbs.push({ label: current.label, href: current.href }, { label: detail });
  }
  return crumbs;
}

/** A grant page link, preselected and coming back to `returnTo` (design 3.4). */
export function grantHref({
  principal,
  scope,
  returnTo,
}: {
  principal?: { kind: "user"; id: string; name: string };
  scope?: { kind: string; id: string; name: string };
  returnTo?: string;
}): string {
  const q = new URLSearchParams();
  if (principal) {
    q.set("principal", `${principal.kind}:${principal.id}`);
    q.set("principalName", principal.name);
  }
  if (scope) {
    q.set("scope", `${scope.kind.toLowerCase()}:${scope.id}`);
    q.set("scopeName", scope.name);
  }
  if (returnTo) q.set("return", returnTo);
  const qs = q.toString();
  return qs ? `${GRANT_HREF}?${qs}` : GRANT_HREF;
}

type SearchParams = Record<string, string | string[] | undefined>;

function one(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

/**
 * Where an old Members link lands (design 5): People, keeping its search.
 * Its views were people, invitations and bindings; the invitation views are
 * People's Invited view now, and bindings moved to each person's Access tab.
 */
export function legacyPeopleHref(params: SearchParams): string {
  const q = new URLSearchParams();
  const view = one(params.view);
  if (view === "invited" || view === "invitations") q.set("view", "invited");
  const search = one(params.q);
  if (search) q.set("q", search);
  const qs = q.toString();
  return qs ? `${PEOPLE_HREF}?${qs}` : PEOPLE_HREF;
}

/**
 * Where an old Teams link lands: the team's page for `?team=<slug>` (the
 * workspace nav tree's link), else Teams with the old table's search
 * (`team-q`) as the list's.
 */
export function legacyTeamsHref(params: SearchParams): string {
  const team = one(params.team);
  if (team) return `${TEAMS_HREF}/${encodeURIComponent(team)}`;
  const search = one(params["team-q"]) ?? one(params.q);
  return search ? `${TEAMS_HREF}?q=${encodeURIComponent(search)}` : TEAMS_HREF;
}

const SCOPE_KINDS = ["ORG", "TEAM", "PROJECT", "APP"] as const;

/**
 * The grant page's preselection, read back from `grantHref`'s query: a user
 * to grant to, a scope to grant at, and where to go after. `return` must be
 * a path in this app, so the page can never send someone off-site.
 */
export function parseGrantParams(params: URLSearchParams): {
  principal: { kind: "user"; id: string; name: string } | null;
  scope: { kind: (typeof SCOPE_KINDS)[number]; id: string; name: string } | null;
  returnTo: string;
} {
  const rawPrincipal = params.get("principal") ?? "";
  const [pKind, ...pRest] = rawPrincipal.split(":");
  const pId = pRest.join(":");
  const principal =
    pKind === "user" && pId
      ? { kind: "user" as const, id: pId, name: params.get("principalName") || pId }
      : null;

  const rawScope = params.get("scope") ?? "";
  const [sKind, ...sRest] = rawScope.split(":");
  const sId = sRest.join(":");
  const kind = SCOPE_KINDS.find((k) => k === sKind?.toUpperCase());
  const scope = kind && sId ? { kind, id: sId, name: params.get("scopeName") || sId } : null;

  const ret = params.get("return") ?? "";
  const returnTo = ret.startsWith("/") && !ret.startsWith("//") ? ret : PEOPLE_HREF;
  return { principal, scope, returnTo };
}
