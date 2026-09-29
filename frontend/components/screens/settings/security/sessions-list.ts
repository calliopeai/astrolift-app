/**
 * Settings › Security › Active sessions (spec 44 §5.1): the embedded list's
 * declaration and its client-side step. `astroliftActiveSessions` returns
 * the viewer's sessions at once with no arguments, so search, the filters,
 * sort and numbered pages run in the client (needsBackend: a Page field).
 * Every session here is the viewer's own, so Mine is the same as All.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftActiveSession } from "@/graphql/identity/identity.types";

const KINDS = ["web", "cli", "mobile", "browser_extension", "api_token"];

export const SESSIONS_LIST: ListDefinition = {
  id: "settings.security.sessions",
  fields: [
    {
      key: "kind",
      label: "Kind",
      options: KINDS.map((k) => ({ value: k, label: k.replace(/_/g, " ") })),
    },
    {
      key: "status",
      label: "Status",
      options: [
        { value: "current", label: "this session" },
        { value: "other", label: "other" },
      ],
    },
  ],
  searchPlaceholder: "Search sessions, labels…",
  defaultSort: [{ key: "lastSeen", dir: "desc" }],
  views: standardViews({}, [], {
    mineNote: "Every session here is yours, so Mine holds the same sessions as All.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const SESSIONS_SELECT: SelectRowsSpec<AstroliftActiveSession> = {
  filter: {
    kind: (s, v) => s.clientKind === v,
    status: (s, v) => (s.isCurrent ? "current" : "other") === v,
  },
  text: (s) => [s.label, s.clientKind],
  sort: {
    lastSeen: (s) => (s.lastSeenAt ? Date.parse(s.lastSeenAt) : 0),
    expires: (s) => (s.expiresAt ? Date.parse(s.expiresAt) : 0),
    kind: (s) => s.clientKind,
  },
  id: (s) => s.id,
};
