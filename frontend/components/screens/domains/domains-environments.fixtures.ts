import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { ManagedDomainDetailProps } from "./ManagedDomainDetail";
import type { ManagedDomainsScreenProps } from "./ManagedDomainsScreen";

/**
 * Hand-typed fixtures for managed domains and environments (group
 * domains-environments).
 */

const noop = () => {};
const noopAsync = async () => {};
const yes = async () => true;
const json = (value: unknown) => value;
/** provisionNameservers is the same JSON scalar, narrowed to string[] in clusters.types. */
const nameservers = (value: string[]) => value as AstroliftManagedDomain["provisionNameservers"];

export const LONG =
  "customer-facing-checkout-experience-for-the-emea-storefront-with-a-deliberately-long-name";

/* ---------------------------------------------------------------- domains */

export const DOMAIN_ACTIVE: AstroliftManagedDomain = {
  id: "5d0c1a2e-0000-4000-8000-000000000001",
  version: 7,
  zone: "apps.acme.example",
  dnsDriver: "route53",
  defaultFor: "tenant_apps",
  isWildcardManaged: true,
  organizationSlug: "acme",
  provisionClusterId: "c0ffee00-0000-4000-8000-000000000001",
  provisionState: "mark_active",
  provisionNameservers: nameservers([
    "ns-1024.awsdns-00.org",
    "ns-1536.awsdns-00.co.uk",
    "ns-512.awsdns-00.net",
    "ns-0.awsdns-00.com",
  ]),
  provisionValidationRecords: json([]) as AstroliftManagedDomain["provisionValidationRecords"],
  challengeRecordName: "_astrolift-challenge.apps.acme.example",
  challengeRecordValue: "astrolift-verify=4f6a1c",
  delegationCheck: json({ delegated: true, checkedAt: "2026-09-27T12:00:00Z" }),
  verificationState: "verified",
  verifiedAt: "2026-09-02T10:15:00Z",
  createdAt: "2026-09-01T09:00:00Z",
};

/** Zone created, workflow still running: NS records not back yet. */
export const DOMAIN_PROVISIONING: AstroliftManagedDomain = {
  ...DOMAIN_ACTIVE,
  id: "5d0c1a2e-0000-4000-8000-000000000002",
  zone: "preview.acme.example",
  dnsDriver: "cloud_dns",
  defaultFor: "preview_envs",
  isWildcardManaged: false,
  provisionState: "create_zone",
  provisionNameservers: nameservers([]),
  delegationCheck: json({}),
  verificationState: "pending",
  verifiedAt: null,
  createdAt: "2026-09-27T14:00:00Z",
};

/** Seeded by the install playbook, never provisioned: no cluster to revalidate from. */
export const DOMAIN_UNPROVISIONED: AstroliftManagedDomain = {
  ...DOMAIN_ACTIVE,
  id: "5d0c1a2e-0000-4000-8000-000000000003",
  zone: "internal.acme.example",
  dnsDriver: "external_dns",
  defaultFor: "none",
  isWildcardManaged: false,
  provisionClusterId: null,
  provisionState: "",
  provisionNameservers: nameservers([]),
  delegationCheck: json(null),
  verificationState: "",
  verifiedAt: null,
  createdAt: "2026-08-15T09:00:00Z",
};

export const DOMAIN_LONG: AstroliftManagedDomain = {
  ...DOMAIN_ACTIVE,
  id: "5d0c1a2e-0000-4000-8000-000000000004",
  zone: `${LONG}.apps.acme.example`,
  defaultFor: "both",
  organizationSlug: `${LONG}-organization`,
  provisionState: "await_delegation_verification",
  provisionNameservers: nameservers([
    "ns-cloud-a1.googledomains-with-a-long-regional-suffix.example.com",
    "ns-cloud-a2.googledomains-with-a-long-regional-suffix.example.com",
  ]),
};

/** The screen's props less the list and its page; the story pages `domains`. */
export type ManagedDomainsFixture = Omit<
  ManagedDomainsScreenProps,
  "list" | "rows" | "totalCount"
> & {
  domains: AstroliftManagedDomain[];
};

export const MANAGED_DOMAINS: ManagedDomainsFixture = {
  loading: false,
  error: null,
  onRetry: noop,
  domains: [DOMAIN_ACTIVE, DOMAIN_PROVISIONING, DOMAIN_UNPROVISIONED],
  creating: false,
  canCreate: true,
  deleting: false,
  revalidating: false,
  onCreate: yes,
  onDelete: noopAsync,
  onRevalidate: noopAsync,
  onCopyNameservers: noopAsync,
  onOpen: noop,
};

export const DOMAIN_DIAGNOSTICS: NonNullable<ManagedDomainDetailProps["diagnostics"]> = {
  id: DOMAIN_ACTIVE.id,
  version: 7,
  zone: DOMAIN_ACTIVE.zone,
  verificationState: "pending",
  provisionState: "mark_active",
  provisionClusterId: DOMAIN_ACTIVE.provisionClusterId ?? null,
  checkedAt: "2026-09-27T12:00:00Z",
  checks: [
    {
      key: "delegation",
      state: "MISMATCH",
      perspective: "public resolver",
      checkedAt: "2026-09-27T12:00:00Z",
      reason: "Public nameservers differ from the configured provider zone.",
      expected: DOMAIN_ACTIVE.provisionNameservers,
      observed: ["olivia.ns.cloudflare.com", "sam.ns.cloudflare.com"],
    },
  ],
  providerZone: {
    state: "OK",
    reason: "Configured public provider zone observed.",
    checkedAt: "2026-09-27T12:00:00Z",
    bindingSource: "PLATFORM_WRITTEN",
    zoneId: "Z-FIXTURE",
    zoneName: DOMAIN_ACTIVE.zone,
    privateZone: false,
    nameservers: DOMAIN_ACTIVE.provisionNameservers,
    truncated: false,
    records: [
      {
        name: DOMAIN_ACTIVE.zone,
        type: "NS",
        ttl: 172800,
        values: DOMAIN_ACTIVE.provisionNameservers,
        aliasTarget: null,
        aliasZoneId: null,
        evaluateTargetHealth: null,
      },
      {
        name: DOMAIN_ACTIVE.zone,
        type: "SOA",
        ttl: 900,
        values: ["ns-0.awsdns-00.com. hostmaster.example. 1 7200 900 1209600 86400"],
        aliasTarget: null,
        aliasZoneId: null,
        evaluateTargetHealth: null,
      },
    ],
  },
  routes: [
    {
      appId: "a0000000-0000-4000-8000-000000000001",
      appName: "Storefront",
      appSlug: "storefront",
      environmentId: "e0000000-0000-4000-8000-000000000001",
      environmentName: "production",
      recordedUrl: "https://storefront.apps.acme.example",
      hostname: "storefront.apps.acme.example",
      clusterId: DOMAIN_ACTIVE.provisionClusterId ?? null,
      clusterName: "West",
      clusterSlug: "west",
      ingressClass: "nginx",
      observedState: "UNKNOWN",
    },
  ],
  routesTruncated: false,
  actions: { canCreate: true, canDelete: true, canRevalidate: true },
};

export const MANAGED_DOMAIN: ManagedDomainDetailProps = {
  id: DOMAIN_ACTIVE.id,
  loading: false,
  domain: DOMAIN_ACTIVE,
  error: null,
  onRetry: noop,
  diagnostics: DOMAIN_DIAGNOSTICS,
  diagnosticsLoading: false,
  diagnosticsError: null,
  onRefreshDiagnostics: noop,
  probe: null,
  probeLoading: false,
  probeError: null,
  onProbe: noopAsync,
  onResetProbe: noop,
  onRevalidate: noopAsync,
  revalidating: false,
  deleting: false,
  verifying: false,
  canVerify: false,
  onVerify: noopAsync,
  onDelete: async () => true,
  removed: false,
  actionMessage: null,
  actionError: null,
};

/* ----------------------------------------------------------- environments */

export const ENV_PROD: AstroliftAppEnvironment = {
  id: "e0000000-0000-4000-8000-000000000001",
  name: "prod",
  registeredAppSlug: "storefront",
  kind: "production",
  region: "us-west-2",
  ownedByMe: false,
  url: "https://storefront.apps.acme.example",
  clusterId: "c0ffee00-0000-4000-8000-000000000001",
  clusterSlug: "prod-west",
  clusterProviderPluginSlug: "aws",
  domainZone: "apps.acme.example",
  ingressPaused: false,
  deploysPaused: false,
  requiredApprovals: 2,
  settings: [
    { id: "s-1", key: "REPLICAS", value: "3" },
    { id: "s-2", key: "LOG_LEVEL", value: "info" },
  ],
  createdAt: "2026-08-02T17:30:00Z",
};

export const ENV_STAGING_PAUSED: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "e0000000-0000-4000-8000-000000000002",
  name: "staging",
  url: "https://storefront-staging.apps.acme.example",
  deploysPaused: true,
  ingressPaused: true,
  requiredApprovals: 0,
  settings: [],
};

/** Registered but not placed on a cluster yet: no URL, no cluster, no zone. */
export const ENV_UNPLACED: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "e0000000-0000-4000-8000-000000000003",
  name: "dev",
  registeredAppSlug: "billing-api",
  url: "",
  clusterId: null,
  clusterSlug: null,
  clusterProviderPluginSlug: null,
  domainZone: null,
  requiredApprovals: 0,
  settings: [],
};

export const ENV_LONG: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "e0000000-0000-4000-8000-000000000004",
  name: `${LONG}-env`,
  registeredAppSlug: LONG,
  url: `https://${LONG}.apps.acme.example`,
  clusterSlug: `${LONG}-cluster`,
  clusterProviderPluginSlug: "on-prem-kubernetes-with-a-long-plugin-slug",
  domainZone: `${LONG}.apps.acme.example`,
  settings: [
    { id: "s-l1", key: `${LONG.toUpperCase().replace(/-/g, "_")}_KEY`, value: `${LONG}-value` },
  ],
};
