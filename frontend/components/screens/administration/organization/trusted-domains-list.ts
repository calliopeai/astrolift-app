/**
 * Admin › Organization › Trusted domains (spec 44 §5.1): the embedded
 * list's declaration and its client-side step. The allowlist field returns
 * every domain at once with no arguments, so search, the mode filter, sort
 * and numbered pages all run in the client (needsBackend: a Page field).
 * Domains record no creator, so Mine is empty.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftOrganizationAllowlistedDomain } from "@/graphql/identity/identity.types";

export const TRUSTED_DOMAINS_LIST: ListDefinition = {
  id: "admin.organization.trusted-domains",
  fields: [
    {
      key: "mode",
      label: "Mode",
      options: [
        { value: "auto", label: "auto-join" },
        { value: "review", label: "review required" },
      ],
    },
    { key: "role", label: "Default role" },
  ],
  searchPlaceholder: "Search domains, roles…",
  defaultSort: [{ key: "domain", dir: "asc" }],
  views: standardViews({ createdBy: "me" }, [], {
    mineNote: "Trusted domains do not record who added them yet, so Mine is empty.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const TRUSTED_DOMAINS_SELECT: SelectRowsSpec<AstroliftOrganizationAllowlistedDomain> = {
  filter: {
    createdBy: () => false,
    mode: (r, v) => (r.requiresReview ? "review" : "auto") === v,
    role: (r, v) => r.defaultRoleSlug === v,
  },
  text: (r) => [r.domain, r.defaultRoleSlug],
  sort: {
    domain: (r) => r.domain.toLowerCase(),
    role: (r) => r.defaultRoleSlug ?? "",
  },
  id: (r) => r.id,
};
