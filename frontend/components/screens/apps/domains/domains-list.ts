/**
 * The app's Domains tab as a list (spec 44 §5.1; Leo's list rule 4).
 *
 * `astroliftAppDomains` returns every domain of the app at once, with no
 * filter, sort or page, so the hook reads the whole set and this answers the
 * rest in the browser, with numbered pages and the view's note saying so.
 * Pure.
 */
import type { ListDefinition } from "@/components/list/list-state";

import { CLIENT_LIST_NOTE, type ClientListSpec, selectClientRows } from "../client-list";
import type { AppDomain } from "./use-app-domains";

export const APP_DOMAINS_LIST: ListDefinition = {
  id: "apps.domains",
  fields: [
    {
      key: "status",
      label: "Status",
      options: [
        { value: "pending", label: "Awaiting DNS" },
        { value: "validating", label: "Validating" },
        { value: "validated", label: "Validated" },
        { value: "failed", label: "Failed" },
      ],
    },
    {
      key: "kind",
      label: "Kind",
      options: [
        { value: "exact", label: "Exact host" },
        { value: "wildcard", label: "Wildcard" },
      ],
    },
  ],
  searchPlaceholder: "Search hostnames…",
  defaultSort: [{ key: "hostname", dir: "asc" }],
  // A domain has no owner of its own, so there is no Mine: one view, and the
  // list draws no view picker.
  views: [{ key: "all", label: "All", filters: {}, note: CLIENT_LIST_NOTE }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const DOMAINS_SPEC: ClientListSpec<AppDomain> = {
  matches: (d, key, value) => {
    if (key === "status") return d.certState === value;
    if (key === "kind") return value === "wildcard" ? Boolean(d.isWildcard) : !d.isWildcard;
    return true;
  },
  text: (d) => [d.hostname, d.certState],
  compare: (a, b, key) => {
    switch (key) {
      case "status":
        return a.certState.localeCompare(b.certState);
      case "checked":
        return (Date.parse(a.lastCheckedAt ?? "") || 0) - (Date.parse(b.lastCheckedAt ?? "") || 0);
      case "hostname":
      default:
        return a.hostname.localeCompare(b.hostname);
    }
  },
  tie: (a, b) => a.id.localeCompare(b.id),
};

export function selectDomains(
  all: AppDomain[],
  filters: Record<string, string>,
  state: Parameters<typeof selectClientRows>[2]
) {
  return selectClientRows(all, filters, state, DOMAINS_SPEC);
}
