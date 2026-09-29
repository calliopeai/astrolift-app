/**
 * Admin › Domains (spec 44 §5.1): the list declaration and its client-side
 * step. `astroliftManagedDomains` returns every zone at once with no
 * arguments, so search, the filters, sort and numbered pages all run in the
 * client (needsBackend: a Page field). Zones record no owner, so Mine is
 * empty until they do.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";

/** A zone's provisioning as one word: active, provisioning, or not provisioned. */
export function zoneState(d: Pick<AstroliftManagedDomain, "provisionState">): string {
  if (d.provisionState === "mark_active") return "active";
  if (d.provisionState === "") return "unprovisioned";
  return "provisioning";
}

export const MANAGED_DOMAINS_LIST: ListDefinition = {
  id: "admin.domains",
  fields: [
    {
      key: "state",
      label: "Status",
      options: [
        { value: "active", label: "active" },
        { value: "provisioning", label: "provisioning" },
        { value: "unprovisioned", label: "not provisioned" },
      ],
    },
    {
      key: "driver",
      label: "DNS driver",
      options: ["route53", "cloud_dns", "external_dns"].map((d) => ({ value: d, label: d })),
    },
    {
      key: "defaultFor",
      label: "Default for",
      options: ["tenant_apps", "preview_envs", "both", "none"].map((d) => ({
        value: d,
        label: d.replace(/_/g, " "),
      })),
    },
  ],
  searchPlaceholder: "Search zones, nameservers…",
  defaultSort: [{ key: "zone", dir: "asc" }],
  views: standardViews(
    { owner: "me" },
    [{ key: "pending", label: "Not active", filters: { pending: "yes" } }],
    { mineNote: "Managed domains do not record who added them yet, so Mine is empty." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const MANAGED_DOMAINS_SELECT: SelectRowsSpec<AstroliftManagedDomain> = {
  filter: {
    owner: () => false,
    pending: (d) => zoneState(d) !== "active",
    state: (d, v) => zoneState(d) === v,
    driver: (d, v) => d.dnsDriver === v,
    defaultFor: (d, v) => d.defaultFor === v,
  },
  text: (d) => [d.zone, d.dnsDriver, ...d.provisionNameservers],
  sort: {
    zone: (d) => d.zone.toLowerCase(),
    driver: (d) => d.dnsDriver,
    created: (d) => Date.parse(d.createdAt),
  },
  id: (d) => d.id,
};
