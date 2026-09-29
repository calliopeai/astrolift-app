/**
 * Cluster › Settings › Sign-in users (spec 44 §5.1): the embedded list's
 * declaration and its client-side step. The provider's user list comes back
 * whole with no cursor, so search, the group and state filters, sort and
 * numbered pages run in the client (needsBackend: a Page field on
 * `astroliftClusterAuthUsers`). The users are the cluster's logins, not
 * platform accounts, so Mine is empty.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { AstroliftClusterAuthUser } from "@/graphql/__generated__/schema";

export const AUTH_USERS_LIST: ListDefinition = {
  id: "clusters.settings.auth-users",
  fields: [
    { key: "group", label: "Group" },
    {
      key: "state",
      label: "State",
      options: [
        { value: "enabled", label: "enabled" },
        { value: "disabled", label: "disabled" },
      ],
    },
  ],
  searchPlaceholder: "Search emails, usernames, groups…",
  defaultSort: [{ key: "user", dir: "asc" }],
  views: standardViews(
    { owner: "me" },
    [{ key: "disabled", label: "Disabled", filters: { state: "disabled" } }],
    { mineNote: "These are the cluster's sign-in users, not platform accounts, so Mine is empty." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const AUTH_USERS_SELECT: SelectRowsSpec<AstroliftClusterAuthUser> = {
  filter: {
    owner: () => false,
    group: (u, v) => u.groups.includes(v),
    state: (u, v) => (u.enabled ? "enabled" : "disabled") === v,
  },
  text: (u) => [u.email, u.username, ...u.groups],
  sort: {
    user: (u) => (u.email || u.username).toLowerCase(),
    status: (u) => u.status,
  },
  id: (u) => u.username,
};
