import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import type { AccessCardViewProps, AccessEditorViewProps } from "./AccessCard";
import type { AppPreviewsScreenProps } from "./AppPreviewsScreen";
import type { AppSecurityScreenProps } from "./AppSecurityScreen";
import type { AstroliftEvent } from "./use-app-security";

/**
 * Hand-typed fixtures for the app security tab (signing, SBOM, scan, policy,
 * access) and the app previews tab. The app record carries only the fields
 * these views read.
 */

const noop = async () => {};
const yes = async () => true;
const setter = () => {};

export const LONG =
  "platform-team-shared-production-checkout-service-with-a-deliberately-long-name-that-keeps-going";

/** An ISO timestamp `seconds` before now, so countdowns and stale checks read sensibly. */
function ago(seconds: number) {
  return new Date(Date.now() - seconds * 1000).toISOString();
}
function ahead(seconds: number) {
  return new Date(Date.now() + seconds * 1000).toISOString();
}

export const APP = {
  id: "app-1",
  slug: "checkout",
  name: "Checkout",
  subdomain: "checkout",
  sourceUrl: "https://github.com/acme/checkout",
  previewEnabled: true,
  previewMaxActive: 5,
  securityPolicy: {
    blockOnCriticalCves: true,
    blockOnMissingSignature: true,
    blockOnHighCveThreshold: 3,
  },
} as AstroliftRegisteredApp;

// ─── Security ────────────────────────────────────────────────────────────────

const DIGEST = "sha256:4f1c9a2be07d3c5e8a61f0b92d7e4c3a1b5f6e7d8c9a0b1c2d3e4f5a6b7c8d9e";

export const SIGNING_EVENT: AstroliftEvent = {
  id: "ev-1",
  eventType: "image.signed",
  registeredAppId: "app-1",
  occurredAt: "2026-09-27T14:05:00Z",
  payload: {
    image_digest: DIGEST,
    image_tag: "ghcr.io/acme/checkout:f1f9f11a",
    signer_identity:
      "https://github.com/acme/checkout/.github/workflows/release.yml@refs/heads/main",
    rekor_log_index: 118_402_977,
    rekor_entry_url: "https://search.sigstore.dev/?logIndex=118402977",
    signed_at: "2026-09-27T14:05:00Z",
  },
};

export const SBOM_EVENT: AstroliftEvent = {
  id: "ev-2",
  eventType: "sbom.generated",
  registeredAppId: "app-1",
  occurredAt: "2026-09-27T14:05:30Z",
  payload: {
    image_digest: DIGEST,
    format: "spdx-json",
    artifact_url: "https://artifacts.example.com/checkout/sbom.spdx.json",
    component_count: 412,
    generated_at: "2026-09-27T14:05:30Z",
  },
};

export const SCAN_EVENT: AstroliftEvent = {
  id: "ev-3",
  eventType: "image.scanned",
  registeredAppId: "app-1",
  occurredAt: "2026-09-27T14:06:00Z",
  payload: {
    image_digest: DIGEST,
    scanned_at: "2026-09-27T14:06:00Z",
    counts: { critical: 1, high: 2, medium: 1, low: 1 },
    findings: [
      {
        cve_id: "CVE-2026-1204",
        severity: "medium",
        package_name: "libxml2",
        package_version: "2.12.5",
        fixed_in_version: "2.12.7",
      },
      {
        cve_id: "CVE-2026-0931",
        severity: "critical",
        package_name: "openssl",
        package_version: "3.0.13",
        fixed_in_version: "3.0.15",
      },
      {
        cve_id: "CVE-2025-4410",
        severity: "high",
        package_name: "zlib",
        package_version: "1.3",
        fixed_in_version: null,
      },
      {
        cve_id: "CVE-2025-3920",
        severity: "high",
        package_name: "busybox",
        package_version: "1.36.1",
        fixed_in_version: "1.36.2",
      },
      {
        cve_id: "CVE-2024-9001",
        severity: "low",
        package_name: "tzdata",
        package_version: "2024a",
        fixed_in_version: null,
      },
    ],
  },
};

export const SECURITY: AppSecurityScreenProps = {
  slug: "checkout",
  app: APP,
  loading: false,
  eventsLoading: false,
  error: null,
  onRetry: () => {},
  eventsError: null,
  onRetryEvents: () => {},
  latestSigning: SIGNING_EVENT,
  latestSbom: SBOM_EVENT,
  latestScan: SCAN_EVENT,
  savingPolicy: false,
  onSavePolicy: yes,
};

/** Every string at its longest: app name, tag, signer, package, CVE id. */
export const SECURITY_LONG: AppSecurityScreenProps = {
  ...SECURITY,
  app: { ...APP, name: LONG, slug: LONG } as AstroliftRegisteredApp,
  latestSigning: {
    ...SIGNING_EVENT,
    payload: {
      ...(SIGNING_EVENT.payload as object),
      image_tag: `ghcr.io/acme/${LONG}:f1f9f11a-${LONG}`,
      signer_identity: `https://github.com/acme/${LONG}/.github/workflows/release.yml@refs/heads/${LONG}`,
    },
  },
  latestScan: {
    ...SCAN_EVENT,
    payload: {
      ...(SCAN_EVENT.payload as object),
      counts: { critical: 1, high: 0, medium: 0, low: 0 },
      findings: [
        {
          cve_id: "CVE-2026-1234567890",
          severity: "critical",
          package_name: LONG,
          package_version: `1.0.0-${LONG}`,
          fixed_in_version: `1.0.1-${LONG}`,
        },
      ],
    },
  },
};

// ─── Access ──────────────────────────────────────────────────────────────────

export const ACCESS_OPEN: AccessCardViewProps = {
  loading: false,
  access: {
    appSlug: "checkout",
    groups: [],
    users: [],
    restricted: false,
    managedByManifest: false,
    enforcedOn: ["astrolift-conflict"],
  },
};

export const ACCESS_RESTRICTED: AccessCardViewProps = {
  loading: false,
  access: {
    appSlug: "checkout",
    groups: ["payments", "platform"],
    users: ["ada@acme.example", "grace@acme.example"],
    restricted: true,
    managedByManifest: false,
    enforcedOn: ["astrolift-conflict"],
  },
};

export const ACCESS_MANIFEST: AccessCardViewProps = {
  loading: false,
  access: { ...ACCESS_RESTRICTED.access!, managedByManifest: true, enforcedOn: [] },
};

export const ACCESS_LONG: AccessCardViewProps = {
  loading: false,
  access: {
    ...ACCESS_RESTRICTED.access!,
    managedByManifest: true,
    groups: [LONG, "platform"],
    users: [`${LONG}@acme.example`],
  },
};

export const EDITOR: AccessEditorViewProps = {
  groups: ["payments", "platform"],
  setGroups: setter,
  users: ["ada@acme.example"],
  setUsers: setter,
  changed: false,
  preview: null,
  saving: false,
  onSave: noop,
};

/** An edited rule, with the live preview saying who would be locked out. */
export const EDITOR_CHANGED: AccessEditorViewProps = {
  ...EDITOR,
  groups: ["payments"],
  changed: true,
  preview: {
    allowed: 14,
    total: 42,
    losing: [
      "ada@acme.example",
      "grace@acme.example",
      "linus@acme.example",
      "ken@acme.example",
      "barbara@acme.example",
      "dennis@acme.example",
    ],
  },
};

// ─── Previews ────────────────────────────────────────────────────────────────

const PREVIEW_BASE: AstroliftPreviewEnvironment = {
  version: 1,
  environmentStatus: "unavailable",
  runtimeStatus: "available",
  id: "pv-1",
  registeredAppSlug: "checkout",
  prNumber: 412,
  prUrl: "https://github.com/acme/checkout/pull/412",
  branch: "feature/apple-pay",
  commitSha: "f1f9f11a2b3c4d5e",
  namespace: "checkout-pr-412",
  hostname: "pr-412.checkout.astrolift.example.com",
  sourceUrl: "https://github.com/acme/checkout",
  status: "running",
  isManual: false,
  isPinned: false,
  pinReason: "",
  pinnedAt: null,
  pinnedByEmail: null,
  tornDownAt: null,
  lastDeployedAt: ago(3 * 3600),
  ttlUntil: ahead(2 * 86400 + 5 * 3600),
  aggregateResources: { podCount: 3, cpuCores: 0.75, memoryBytes: 1.5 * 1024 ** 3 },
  estimatedDailyCostUsd: 1.84,
  estimatedCostApproximate: false,
  estimatedCostNotes: [],
  openedByLogin: "barbara",
  openedByMe: false,
  failureReason: "",
};

export const PREVIEW_ROWS: AstroliftPreviewEnvironment[] = [
  PREVIEW_BASE,
  {
    ...PREVIEW_BASE,
    id: "pv-2",
    prNumber: 0,
    prUrl: "",
    branch: "spike/new-cart",
    namespace: "checkout-spike-new-cart",
    hostname: "spike-new-cart.checkout.astrolift.example.com",
    isManual: true,
    lastDeployedAt: ago(9 * 86400),
    ttlUntil: ahead(40 * 60),
    estimatedDailyCostUsd: 0.62,
    estimatedCostApproximate: true,
    estimatedCostNotes: ["Summed every SKU in the service; an over-count."],
  },
  {
    ...PREVIEW_BASE,
    id: "pv-3",
    prNumber: 409,
    prUrl: "https://github.com/acme/checkout/pull/409",
    branch: "fix/tax-rounding",
    namespace: "checkout-pr-409",
    hostname: "pr-409.checkout.astrolift.example.com",
    status: "failed",
    aggregateResources: { podCount: 0, cpuCores: 0, memoryBytes: 0 },
    estimatedDailyCostUsd: null,
  },
  {
    ...PREVIEW_BASE,
    id: "pv-4",
    prNumber: 401,
    prUrl: "https://github.com/acme/checkout/pull/401",
    branch: "chore/deps",
    namespace: "checkout-pr-401",
    hostname: "pr-401.checkout.astrolift.example.com",
    status: "torn_down",
    tornDownAt: ago(86400),
  },
];

function spendOf(list: AstroliftPreviewEnvironment[]) {
  const live = list.filter((p) => p.status !== "torn_down");
  const priced = live.filter((p) => typeof p.estimatedDailyCostUsd === "number");
  const dailyTotal = priced.reduce((s, p) => s + (p.estimatedDailyCostUsd ?? 0), 0);
  return {
    dailyTotal,
    monthlyProjection: dailyTotal * 30,
    priced: priced.length,
    unpriced: live.length - priced.length,
    approximate: priced.filter((p) => p.estimatedCostApproximate).length,
    liveCount: live.length,
  };
}

/** Everything but the list state, which stories build with `useLocalListState`. */
export type PreviewsFixture = Omit<AppPreviewsScreenProps, "list">;

export const PREVIEWS: PreviewsFixture = {
  slug: "checkout",
  app: APP,
  loading: false,
  rows: PREVIEW_ROWS,
  newRows: { count: 0, onReveal: setter },
  pageLoading: false,
  pageError: null,
  onRetry: setter,
  nextCursor: null,
  totalCount: PREVIEW_ROWS.length,
  previewCount: PREVIEW_ROWS.length,
  counts: { running: 2, failed: 1, tornDown: 1 },
  spend: spendOf(PREVIEW_ROWS),
  stalePreviews: [PREVIEW_ROWS[1]!],
  canDeploy: true,
  tearingDown: false,
  extending: false,
  creating: false,
  onExtend: noop,
  onTearDown: noop,
  onCreate: yes,
  configHref: "/apps/checkout/settings?section=configuration",
};

export const PREVIEWS_EMPTY: PreviewsFixture = {
  ...PREVIEWS,
  rows: [],
  totalCount: 0,
  previewCount: 0,
  counts: { running: 0, failed: 0, tornDown: 0 },
  spend: spendOf([]),
  stalePreviews: [],
};

const LONG_ROW: AstroliftPreviewEnvironment = {
  ...PREVIEW_BASE,
  branch: `feature/${LONG}`,
  namespace: `checkout-${LONG}`,
  hostname: `pr-412.${LONG}.astrolift.example.com`,
  commitSha: "5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
};

export const PREVIEWS_LONG: PreviewsFixture = {
  ...PREVIEWS,
  app: { ...APP, name: LONG, slug: LONG, subdomain: LONG } as AstroliftRegisteredApp,
  rows: [LONG_ROW],
  totalCount: 1,
  previewCount: 1,
  counts: { running: 1, failed: 0, tornDown: 0 },
  spend: spendOf([LONG_ROW]),
  stalePreviews: [],
};
