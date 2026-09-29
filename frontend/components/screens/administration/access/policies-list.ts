import { type ListDefinition, standardViews } from "@/components/list/list-state";

import { one } from "./identity-lists";

/**
 * Admin › Policies (spec 44 §5.1) on `astroliftPoliciesPage`'s list
 * contract (#2153): views All · Mine (policies you created) · Deny · Allow,
 * a scope chip, the column sorts and numbered pages. Newest first.
 */
export const POLICIES_LIST: ListDefinition = {
  id: "admin.policies",
  fields: [
    {
      key: "effect",
      label: "Effect",
      options: [
        { value: "DENY", label: "deny" },
        { value: "ALLOW", label: "allow" },
      ],
    },
    {
      key: "scope",
      label: "Scope",
      options: [
        { value: "ORG", label: "organization" },
        { value: "TEAM", label: "team" },
        { value: "PROJECT", label: "project" },
        { value: "APP", label: "app" },
      ],
    },
  ],
  // The server matches name, slug, description and action.
  searchPlaceholder: "Search policies…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ createdBy: "me" }, [
    { key: "deny", label: "Deny", filters: { effect: "DENY" } },
    { key: "allow", label: "Allow", filters: { effect: "ALLOW" } },
  ]),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export interface PoliciesListFilter {
  effect?: string[];
  scopeLevel?: string[];
  createdBy?: string[];
}

export function policiesFilter(filters: Record<string, string>): PoliciesListFilter {
  return {
    effect: one(filters.effect),
    scopeLevel: one(filters.scope),
    createdBy: one(filters.createdBy),
  };
}
