/**
 * Admin › Providers › Source (spec 44 §5.1): the two lists, each in its own
 * section. Neither page field takes a sort argument (both seek on
 * `-created_at, -guid`), so the order is the server's, newest first.
 *
 * Hosts: `astroliftSourceConnectionsPage` takes `search`, `limit`, `after`.
 * Kind and Status narrow a wider page (`NARROW_LIMIT`) in the client; Mine
 * is your personal (OAuth user or PAT) connections, narrowed the same way.
 *
 * SSH deploy keys: `astroliftSshDeployKeysPage` also takes `appSlug`, where
 * `null` is every key and `""` the org-scoped ones only, so Scope goes to
 * the server. Keys record no creator, so Mine is empty until they do.
 */
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

export const NARROW_LIMIT = 100;

const HOST_FAMILIES = ["github", "gitlab", "bitbucket", "gitea"];

export const SOURCE_HOSTS_LIST: ListDefinition = {
  id: "admin.providers.hosts",
  fields: [
    {
      key: "host",
      label: "Host",
      options: HOST_FAMILIES.map((h) => ({ value: h, label: h })),
    },
    {
      key: "status",
      label: "Status",
      options: [
        { value: "active", label: "active" },
        { value: "disabled", label: "disabled" },
      ],
    },
  ],
  // The server matches the kind, API base URL, account login and display name.
  searchPlaceholder: "Search connections, accounts, URLs…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ personal: "yes" }, [], {
    mineNote: `Mine keeps your personal connections among the newest ${NARROW_LIMIT}.`,
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

function narrowsHosts(filters: Record<string, string>): boolean {
  return Boolean(filters.host || filters.status || filters.personal);
}

export function hostsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    search: q.trim() || null,
    limit: narrowsHosts(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

export function narrowHosts(
  rows: AstroliftSourceConnection[],
  filters: Record<string, string>
): { rows: AstroliftSourceConnection[]; narrowed: boolean } {
  if (!narrowsHosts(filters)) return { rows, narrowed: false };
  return {
    narrowed: true,
    rows: rows.filter((c) => {
      if (filters.host && !c.kind.startsWith(`${filters.host}_`)) return false;
      if (filters.status && (c.isActive ? "active" : "disabled") !== filters.status) return false;
      if (filters.personal && !c.isPersonal) return false;
      return true;
    }),
  };
}

export const DEPLOY_KEYS_LIST: ListDefinition = {
  id: "admin.providers.keys",
  fields: [
    {
      key: "scope",
      label: "Scope",
      options: [{ value: "org", label: "org-scoped" }],
    },
    { key: "app", label: "App" },
  ],
  // The server matches the name, the SHA-256 fingerprint and the app slug.
  searchPlaceholder: "Search keys, fingerprints, apps…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ createdBy: "me" }, [], {
    mineNote: "Deploy keys do not record who generated them yet, so Mine is empty.",
  }),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

export function keysVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
) {
  return {
    // An app wins over the org scope; `""` asks for org-scoped keys only.
    appSlug: filters.app || (filters.scope === "org" ? "" : null),
    search: q.trim() || null,
    limit: pageSize,
    after,
  };
}
