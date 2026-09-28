import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { AddDomainSheetProps } from "./AddDomainSheet";
import type { DomainHandshakeCardProps } from "./DomainHandshakeCard";
import type { UploadCertSheetProps } from "./UploadCertSheet";
import type { AppDomain, AppDomainsState, ClusterCertificate } from "./use-app-domains";

/** Hand-typed fixtures for the app domains tab (group app-domains). */

const noop = () => {};
const noopAsync = async () => {};
const yes = async () => true;

const DAY = 24 * 60 * 60 * 1000;
/** Relative so the cert expiry badge reads the same whenever the story runs. */
const inDays = (days: number) => new Date(Date.now() + days * DAY).toISOString();

export const LONG =
  "customer-facing-checkout-experience-for-the-emea-storefront-with-a-deliberately-long-subdomain";

export const ENV_PROD: AstroliftAppEnvironment = {
  id: "env-prod",
  name: "prod",
  registeredAppSlug: "storefront",
  url: "https://storefront.apps.prod-west.example.com",
  clusterId: "c0ffee00-0000-4000-8000-000000000001",
  clusterSlug: "prod-west",
  clusterProviderPluginSlug: "aws",
  domainZone: "apps.prod-west.example.com",
  ingressPaused: false,
  deploysPaused: false,
  requiredApprovals: 1,
  settings: [],
  createdAt: "2026-08-02T17:30:00Z",
};

export const ENV_STAGING_PAUSED: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "env-staging",
  name: "staging",
  url: "https://storefront.apps.staging.example.com",
  ingressPaused: true,
};

/** Validated DNS, active cert, redirects + path routes configured. */
export const DOMAIN_ACTIVE: AppDomain = {
  id: "dom-1",
  hostname: "checkout.acme.com",
  certState: "validated",
  validationMethod: "dns_txt",
  validationToken: "astrolift-verify=4f8k2m9q1r7t",
  lastCheckedAt: "2026-09-28T09:12:00Z",
  isActive: true,
  registeredAppSlug: "storefront",
  createdAt: "2026-09-01T10:00:00Z",
  txtChallengeToken: "astrolift-verify=4f8k2m9q1r7t",
  expectedCnameTarget: "storefront.apps.prod-west.example.com",
  isPlatformManagedZone: false,
  lastValidationError: "",
  requiredDnsRecords: [
    {
      kind: "CNAME",
      name: "checkout.acme.com",
      value: "storefront.apps.prod-west.example.com",
      ttl: 300,
      propagated: true,
      lastCheckedAt: "2026-09-28T09:12:00Z",
      message: "",
    },
    {
      kind: "TXT",
      name: "_astrolift.checkout.acme.com",
      value: "astrolift-verify=4f8k2m9q1r7t",
      ttl: 300,
      propagated: true,
      lastCheckedAt: "2026-09-28T09:12:00Z",
      message: "",
    },
  ],
  certificateState: "active",
  lastCertificateError: "",
  byoCertificateUploadedAt: null,
  certExpiresAt: inDays(62),
  certIssuerSerial: "04:3a:9f:1c",
  certObservabilityStatus: "ok",
  redirectRules: [
    {
      id: "rr-1",
      kind: "http_to_https",
      sourcePattern: "",
      destinationUrl: "",
      httpStatus: 301,
      preserveQueryString: true,
      priority: 0,
    },
    {
      id: "rr-2",
      kind: "custom",
      sourcePattern: "/old-cart",
      destinationUrl: "https://checkout.acme.com/cart",
      httpStatus: 302,
      preserveQueryString: false,
      priority: 1,
    },
  ],
  pathRoutes: [
    {
      id: "pr-1",
      pathPrefix: "/api",
      targetWorkloadSlug: "storefront-api",
      targetPort: 8080,
      stripPrefix: true,
      priority: 0,
    },
  ],
  isWildcard: false,
  sniCertRef: "",
  edgeAuthState: "ungated",
};

/** DNS not yet propagated: the operator still has records to add. */
export const DOMAIN_PENDING: AppDomain = {
  ...DOMAIN_ACTIVE,
  id: "dom-2",
  hostname: "shop.acme.com",
  certState: "pending",
  lastCheckedAt: null,
  requiredDnsRecords: [
    {
      kind: "CNAME",
      name: "shop.acme.com",
      value: "storefront.apps.prod-west.example.com",
      ttl: 300,
      propagated: false,
      lastCheckedAt: null,
      message: "No CNAME found",
    },
  ],
  certificateState: "not_requested",
  certExpiresAt: null,
  certObservabilityStatus: "",
  redirectRules: [],
  pathRoutes: [],
  edgeAuthState: "no_gate",
};

/** Validated but auto-issuance failed: the BYO-cert escape hatch shows. */
export const DOMAIN_CERT_FAILED: AppDomain = {
  ...DOMAIN_ACTIVE,
  id: "dom-3",
  hostname: "pay.acme.com",
  certState: "failed",
  lastValidationError: "TXT record _astrolift.pay.acme.com has the wrong value",
  certificateState: "failed",
  lastCertificateError:
    "acme: error: 429 :: urn:ietf:params:acme:error:rateLimited :: too many certificates already issued",
  certExpiresAt: null,
  certObservabilityStatus: "failed",
  redirectRules: [],
  pathRoutes: [],
  edgeAuthState: "no_gate",
};

/** Operator-supplied certificate, expiring inside the warning window. */
export const DOMAIN_BYO: AppDomain = {
  ...DOMAIN_ACTIVE,
  id: "dom-4",
  hostname: "legacy.acme.com",
  certificateState: "byo",
  byoCertificateUploadedAt: "2026-09-20T15:40:00Z",
  certExpiresAt: inDays(10),
  edgeAuthState: "gated",
};

/** Wildcard on a platform-managed zone, cert still issuing. */
export const DOMAIN_WILDCARD: AppDomain = {
  ...DOMAIN_ACTIVE,
  id: "dom-5",
  hostname: "tenant.acme.com",
  certState: "validating",
  validationMethod: "dns_01",
  isPlatformManagedZone: true,
  certificateState: "issuing",
  certExpiresAt: inDays(3),
  isWildcard: true,
  sniCertRef: "arn:aws:acm:us-west-2:123456789012:certificate/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
  redirectRules: [],
  pathRoutes: [],
  edgeAuthState: "no_gate",
};

/** No handshake records returned yet, cert queued. */
export const DOMAIN_NO_RECORDS: AppDomain = {
  ...DOMAIN_PENDING,
  id: "dom-6",
  hostname: "beta.acme.com",
  certState: "validated",
  requiredDnsRecords: [],
  certificateState: "requested",
};

export const DOMAIN_LONG: AppDomain = {
  ...DOMAIN_ACTIVE,
  id: "dom-long",
  hostname: `${LONG}.acme.com`,
  lastValidationError: `Resolver returned SERVFAIL for ${LONG}.acme.com after 3 attempts across 4 nameservers`,
  sniCertRef: `arn:aws:acm:us-west-2:123456789012:certificate/${LONG}`,
  requiredDnsRecords: [
    {
      kind: "CNAME",
      name: `${LONG}.acme.com`,
      value: `${LONG}.apps.prod-west.example.com`,
      ttl: 300,
      propagated: false,
      lastCheckedAt: null,
      message: "",
    },
  ],
  redirectRules: [
    {
      id: "rr-long",
      kind: "custom",
      sourcePattern: `/${LONG}/${LONG}`,
      destinationUrl: `https://${LONG}.acme.com/${LONG}`,
      httpStatus: 301,
      preserveQueryString: true,
      priority: 0,
    },
  ],
  pathRoutes: [
    {
      id: "pr-long",
      pathPrefix: `/${LONG}`,
      targetWorkloadSlug: LONG,
      targetPort: 65535,
      stripPrefix: false,
      priority: 10,
    },
  ],
};

export const WORKLOADS = [
  { slug: "storefront-web", name: "Storefront web" },
  { slug: "storefront-api", name: "Storefront API" },
];

export const CERTS: ClusterCertificate[] = [
  {
    arn: "arn:aws:acm:us-west-2:123456789012:certificate/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    name: "tenant-wildcard",
    domainName: "*.tenant.acme.com",
    status: "ISSUED",
  },
  {
    arn: "arn:aws:acm:us-west-2:123456789012:certificate/9f8e7d6c-5b4a-3928-1716-0f1e2d3c4b5a",
    name: "acme-apex",
    domainName: "acme.com",
    status: "ISSUED",
  },
];

/** The full hook result, as the route would pass it. */
export const APP_DOMAINS: AppDomainsState = {
  loading: false,
  domains: [DOMAIN_ACTIVE, DOMAIN_PENDING, DOMAIN_CERT_FAILED, DOMAIN_BYO, DOMAIN_WILDCARD],
  environments: [ENV_PROD, ENV_STAGING_PAUSED],
  workloadOptions: WORKLOADS,
  busy: false,
  addOpen: false,
  setAddOpen: noop,
  addIsWildcard: false,
  setAddIsWildcard: noop,
  clusterProviderSlug: "aws",
  showCertCombobox: false,
  certs: [],
  certsLoading: false,
  addDomain: yes,
  uploadCertificate: yes,
  removeDomain: noopAsync,
  recheckDomain: noopAsync,
  saveRedirects: yes,
  savePathRoutes: yes,
  toggleIngress: noopAsync,
};

export const HANDSHAKE_CARD: DomainHandshakeCardProps = {
  domain: DOMAIN_ACTIVE,
  busy: false,
  workloadOptions: WORKLOADS,
  onRecheck: noop,
  onRemove: noop,
  onUploadCert: noop,
  onSaveRedirects: yes,
  onSavePathRoutes: yes,
};

export const ADD_SHEET: AddDomainSheetProps = {
  open: true,
  onOpenChange: noop,
  onSubmit: yes,
  busy: false,
  isWildcard: false,
  onWildcardChange: noop,
  clusterProviderSlug: "aws",
  showCertCombobox: false,
  certs: [],
  certsLoading: false,
};

export const UPLOAD_SHEET: UploadCertSheetProps = {
  domain: DOMAIN_CERT_FAILED,
  open: true,
  onOpenChange: noop,
  onSubmit: yes,
  busy: false,
};
