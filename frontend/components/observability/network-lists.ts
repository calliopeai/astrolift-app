/**
 * Logs & metrics › DNS & TLS (spec 44 §5.1): the two cards' embedded list
 * declarations and their client-side step. Both fields return the driver's
 * whole answer at once with no arguments, so search, the filters, sort and
 * numbered pages run in the client (needsBackend: Page fields). Records and
 * certificates are the cluster's, not a person's, so Mine is empty.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type {
  AstroliftAppCertificate,
  AstroliftAppDnsRecord,
} from "@/graphql/lifecycle/lifecycle.types";

const NOT_PERSONAL = "Driver records are the cluster's, not a person's, so Mine is empty.";

export const DNS_RECORDS_LIST: ListDefinition = {
  id: "observability.dns-records",
  fields: [
    {
      key: "type",
      label: "Type",
      options: ["A", "AAAA", "CNAME", "TXT", "MX", "NS"].map((t) => ({ value: t, label: t })),
    },
    {
      key: "propagation",
      label: "Propagation",
      options: ["propagated", "pending", "unknown"].map((p) => ({ value: p, label: p })),
    },
  ],
  searchPlaceholder: "Search names, values…",
  defaultSort: [{ key: "name", dir: "asc" }],
  views: standardViews({ owner: "me" }, [], { mineNote: NOT_PERSONAL }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const dnsKey = (r: AstroliftAppDnsRecord) => `${r.name}|${r.type}|${r.value}`;

export const DNS_RECORDS_SELECT: SelectRowsSpec<AstroliftAppDnsRecord> = {
  filter: {
    owner: () => false,
    type: (r, v) => r.type === v,
    propagation: (r, v) => r.propagationStatus === v,
  },
  text: (r) => [r.name, r.value, r.type],
  sort: {
    name: (r) => r.name.toLowerCase(),
    type: (r) => r.type,
    ttl: (r) => r.ttl,
  },
  id: dnsKey,
};

export const TLS_CERTIFICATES_LIST: ListDefinition = {
  id: "observability.tls-certificates",
  fields: [
    {
      key: "renewal",
      label: "Renewal",
      options: ["auto", "manual", "failed", "unknown"].map((r) => ({ value: r, label: r })),
    },
    {
      key: "expiring",
      label: "Expiring",
      options: [
        { value: "30", label: "within 30 days" },
        { value: "90", label: "within 90 days" },
      ],
    },
  ],
  searchPlaceholder: "Search hostnames, issuers…",
  defaultSort: [{ key: "expires", dir: "asc" }],
  views: standardViews({ owner: "me" }, [], { mineNote: NOT_PERSONAL }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const TLS_CERTIFICATES_SELECT: SelectRowsSpec<AstroliftAppCertificate> = {
  filter: {
    owner: () => false,
    renewal: (c, v) => c.renewalStatus === v,
    expiring: (c, v) => c.daysUntilExpiry < Number(v),
  },
  text: (c) => [c.hostname, c.issuer],
  sort: {
    hostname: (c) => c.hostname.toLowerCase(),
    expires: (c) => c.daysUntilExpiry,
  },
  id: (c) => c.id,
};
