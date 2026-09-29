import type { Crumb } from "@/components/shell/ShellHeader";

import { adminCrumb } from "./admin-crumbs";

/**
 * Where policies live (design 3.6): the list, the stepped New page, and one
 * page per policy that reads it as a sentence and edits it the same way. Pure.
 */
export const POLICIES_HREF = "/administration/policies";
export const NEW_POLICY_HREF = "/administration/policies/new";

export function policyHref(id: string): string {
  return `${POLICIES_HREF}/${encodeURIComponent(id)}`;
}

/** `Admin ▾ › Policies › <page>`, or `Admin ▾ › Policies` on the list. */
export function policiesCrumbs(page?: string): Crumb[] {
  return page
    ? [adminCrumb("policies"), { label: "Policies", href: POLICIES_HREF }, { label: page }]
    : [adminCrumb("policies"), { label: "Policies" }];
}
