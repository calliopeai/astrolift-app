/**
 * "People with access" on an app or agent (design 3.3): the list's
 * declaration and the rows it shows, from today's two grant paths on one
 * app. Pure.
 *
 *   binding      a role binding made on this app (`astroliftRoleBindingsPage`
 *                with `appSlug`), held by a user or an IdP group
 *   team_share   another team's access level on this app (`AppTeamAccess`,
 *                `astroliftAppTeamAccessesPage`), held by the team
 *
 * Grants made on the app's project, team or the org reach it too; the
 * backend cannot list them per app yet (design 6, item 6), so the All view
 * says so rather than showing them.
 */

import {
  type GrantSourceInfo,
  type Principal,
  principalOfBinding,
  sourceOfBinding,
} from "@/components/access/access-model";
import type { ListDefinition } from "@/components/list/use-list-state";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import type { AstroliftAppTeamAccess } from "@/graphql/registry/registry.types";

interface RowBase {
  id: string;
  principal: Principal;
  role: { name: string; slug: string };
  source: GrantSourceInfo;
  grantedAt: string;
  expiresAt: string | null;
}

export type AccessRow =
  | (RowBase & { kind: "binding"; binding: AstroliftRoleBinding })
  | (RowBase & { kind: "team_share"; share: AstroliftAppTeamAccess });

export const APP_ACCESS_LIST: ListDefinition = {
  id: "apps.access.people",
  fields: [],
  searchPlaceholder: "Search people, groups, teams and roles",
  defaultSort: [],
  views: [
    {
      key: "all",
      label: "All",
      filters: {},
      note: "Grants on this app and team shares. Grants on its project, team or the org reach it too and are not listed here yet: check a person to see them.",
    },
    { key: "mine", label: "Mine", filters: {}, note: "Your own grants on this app." },
    { key: "roles", label: "Roles", filters: {} },
    { key: "teams", label: "Team shares", filters: {} },
  ],
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** Which sources a view reads: role bindings, team shares, or both. */
export function viewSources(view: string): { bindings: boolean; shares: boolean } {
  if (view === "teams") return { bindings: false, shares: true };
  if (view === "all") return { bindings: true, shares: true };
  return { bindings: true, shares: false };
}

export function bindingRow(b: AstroliftRoleBinding): AccessRow {
  const principal = principalOfBinding(b);
  return {
    kind: "binding",
    id: `binding:${b.id}`,
    principal:
      principal.kind === "user"
        ? { ...principal, href: `/administration/members/${principal.id}` }
        : principal,
    role: { name: b.role.name || b.role.slug, slug: b.role.slug },
    // The query is narrowed to this app, so every binding is made here.
    source: sourceOfBinding(b),
    grantedAt: b.grantedAt,
    expiresAt: b.expiresAt ?? null,
    binding: b,
  };
}

/** A team share is made on this app, held by the team: direct, principal the team. */
export function shareRow(s: AstroliftAppTeamAccess): AccessRow {
  return {
    kind: "team_share",
    id: `share:${s.id}`,
    principal: {
      kind: "team",
      id: s.teamSlug,
      name: s.teamName || s.teamSlug,
      detail: s.isHome ? "Home team" : undefined,
      href: `/teams/${s.teamSlug}`,
    },
    role: { name: s.accessLevel, slug: s.accessLevel },
    source: {},
    grantedAt: s.createdAt,
    expiresAt: null,
    share: s,
  };
}

/**
 * The rows a view shows. All puts team shares on its first page, above the
 * bindings, since an app is shared with a handful of teams; later pages are
 * bindings only. Mine keeps the viewer's own bindings (the page was searched
 * by their username, which can match others).
 */
export function accessRows({
  view,
  firstPage,
  bindings,
  shares,
  meId,
}: {
  view: string;
  firstPage: boolean;
  bindings: AstroliftRoleBinding[];
  shares: AstroliftAppTeamAccess[];
  meId: string | null;
}): AccessRow[] {
  if (view === "teams") return shares.map(shareRow);
  if (view === "mine") {
    return meId ? bindings.filter((b) => b.user?.id === meId).map(bindingRow) : [];
  }
  const bindingRows = bindings.map(bindingRow);
  return view === "all" && firstPage ? [...shares.map(shareRow), ...bindingRows] : bindingRows;
}
