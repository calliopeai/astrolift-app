/**
 * Who has access on one object (access UX design 3.3): the Access tab of a
 * team, and of an app, project or agent, read from `astroliftAccessOn`
 * (#2157). One row per grant: a user or group binding on the object or on an
 * ancestor that inherits, the org's group mappings, and on an app or agent
 * its team shares, each with the id of the row that grants it. The server
 * searches and numbers the pages; this is the pure half.
 */
import type {
  GrantSourceInfo,
  Principal,
  ScopeKind,
  ScopeRef,
} from "@/components/access/access-model";
import type { ListDefinition } from "@/components/list/list-state";

import { groupParam } from "@/components/screens/members/people-model";

import { PEOPLE_HREF, TEAMS_HREF } from "./access-nav";
import { parseScopeLabel, scopeHref } from "./principal-access";

/** An `AstroliftAccessEntry`, as far as the tab reads it. */
export interface AccessEntry {
  principalKind: string;
  source: string;
  bindingId: string;
  user?: { id: string; username: string; email: string } | null;
  memberId?: string | null;
  groupExternalId?: string | null;
  groupMemberCount?: number | null;
  teamId?: string | null;
  teamSlug?: string | null;
  teamName?: string | null;
  role?: {
    id: string;
    slug: string;
    name: string;
    description?: string;
    scopeLevel: string;
    permissions: string[];
    isSystem: boolean;
  } | null;
  accessLevel?: string | null;
  scopeKind: string;
  sourceScopeLabel: string;
  inherited: boolean;
  expiresAt?: string | null;
}

export const ENTITY_ACCESS_LIST: ListDefinition = {
  id: "admin.access.entity",
  fields: [],
  // The server matches the holder, the role and the scope.
  searchPlaceholder: "Search people, groups, teams and roles…",
  defaultSort: [{ key: "principal", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

/** `astroliftAccessOn` variables: the object's kind and guid, the search, the page. */
export function accessOnVariables(
  target: { kind: ScopeKind | "AGENT"; id: string },
  state: { q: string; page: number; pageSize: number }
) {
  return {
    scopeKind: target.kind,
    scopeId: target.id,
    search: state.q.trim() || null,
    page: Math.max(1, state.page),
    pageSize: state.pageSize,
  };
}

/** A stable row id: one binding, mapping or share can reach one principal once. */
export function entryKey(e: AccessEntry): string {
  return `${e.source}:${e.bindingId}:${e.user?.id ?? e.groupExternalId ?? e.teamId ?? ""}`;
}

export function principalOfEntry(e: AccessEntry): Principal {
  if (e.principalKind === "USER" && e.user) {
    return {
      kind: "user",
      id: e.user.id,
      name: e.user.username,
      detail: e.user.email || undefined,
      href: e.memberId ? `${PEOPLE_HREF}/${e.memberId}` : undefined,
    };
  }
  if (e.principalKind === "GROUP" && e.groupExternalId) {
    return {
      kind: "group",
      id: e.groupExternalId,
      name: e.groupExternalId,
      detail:
        e.groupMemberCount != null
          ? `IdP group · ${e.groupMemberCount} ${e.groupMemberCount === 1 ? "member" : "members"}`
          : "IdP group",
      href: `${PEOPLE_HREF}/${encodeURIComponent(groupParam(e.groupExternalId))}`,
    };
  }
  const slug = e.teamSlug ?? "";
  return {
    kind: "team",
    id: slug || (e.teamId ?? ""),
    name: e.teamName || slug,
    href: slug ? `${TEAMS_HREF}/${encodeURIComponent(slug)}` : undefined,
  };
}

/** Where the grant is held, named from the server's label. */
export function scopeOfEntry(e: AccessEntry): ScopeRef {
  const parsed = parseScopeLabel(e.sourceScopeLabel);
  const kind = (parsed?.kind ?? e.scopeKind) as ScopeKind;
  const name = parsed?.slug ?? e.sourceScopeLabel;
  return { kind, id: name, name, href: parsed ? scopeHref(parsed.kind, parsed.slug) : undefined };
}

/**
 * Why they have it here: a group binding or mapping is via that group, a
 * share is via that team, and a grant held on an ancestor (or through a
 * share) is inherited from where it is held.
 */
export function sourceOfEntry(e: AccessEntry): GrantSourceInfo {
  const source: GrantSourceInfo = {};
  if ((e.source === "GROUP_BINDING" || e.source === "GROUP_MAPPING") && e.groupExternalId) {
    source.via = {
      kind: "group",
      group: e.groupExternalId,
      href: `${PEOPLE_HREF}/${encodeURIComponent(groupParam(e.groupExternalId))}`,
    };
  }
  if (e.source === "TEAM_SHARE" && e.teamSlug) {
    source.via = {
      kind: "team",
      team: e.teamSlug,
      href: `${TEAMS_HREF}/${encodeURIComponent(e.teamSlug)}`,
    };
  }
  if (e.inherited) source.inheritedFrom = scopeOfEntry(e);
  return source;
}

/** What removing the row does: revoke a binding, delete a mapping, or nothing here (a share). */
export type EntryRemoval = "binding" | "mapping" | null;

export function removalOf(e: AccessEntry): EntryRemoval {
  if (e.source === "USER_BINDING" || e.source === "GROUP_BINDING") return "binding";
  if (e.source === "GROUP_MAPPING") return "mapping";
  return null;
}

export const SOURCE_LABEL: Record<string, string> = {
  USER_BINDING: "grant",
  GROUP_BINDING: "group grant",
  GROUP_MAPPING: "group mapping",
  TEAM_SHARE: "team share",
};
