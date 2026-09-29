import type { SortState } from "@/components/data-table";

import type { ClusterDetailProps } from "./ClusterDetail";
import type { ClusterLiveStatsProps } from "./ClusterLiveStats";
import type { ClustersListProps } from "./ClustersList";
import type { RegisterClusterPageProps } from "./RegisterClusterPage";
import type { ClusterActions } from "./use-cluster-actions";
import type { ClusterRow } from "./use-clusters-list";

/** Hand-typed fixtures for the cluster list, detail and register screens. */

const noop = () => {};
const resolved = async () => {};

export function cluster(slug: string, patch: Partial<ClusterRow> = {}): ClusterRow {
  return {
    id: `cl-${slug}`,
    slug,
    name: slug,
    isActive: true,
    lifecycle: "managed",
    lastManagementError: "",
    providerPluginSlug: "eks",
    region: "us-west-2",
    ingressClass: "nginx",
    ingressMode: "shared",
    authMethod: "exec_plugin",
    endpoint: "https://kube.example.com",
    capabilities: {
      cert_manager: { installed: true },
      ingress: { installed: true },
      external_dns: { installed: false },
      storage_classes: ["gp3"],
      metrics_server: { installed: true },
      prometheus: { installed: false },
    },
    capabilitiesProbedAt: "2026-09-27T14:05:00Z",
    managedAt: "2026-09-20T09:00:00Z",
    createdAt: "2026-09-01T12:00:00Z",
    bootstrapRuns: [],
    agentProvisioned: true,
    heartbeatStatus: "connected",
    heartbeatAgeSeconds: 12,
    heartbeatIntervalSeconds: 30,
    lastHeartbeatAt: "2026-09-28T10:00:00Z",
    ...patch,
  };
}

/** The viewer in stories, for the Mine view. */
export const ME = "leo";

export const CLUSTERS: ClusterRow[] = [
  cluster("prd-us-west-2", {
    name: "Production US West",
    createdByUsername: ME,
    lastBootstrapRun: {
      id: "run-1",
      status: "succeeded",
      chartVersion: "0.1.37",
      cliVersion: "3.3.1",
      errorMessage: "",
      endedAt: "2026-09-20T09:04:00Z",
      startedAt: "2026-09-20T09:00:00Z",
      hostInfo: {},
      installedReleases: {},
      triggeredByUsername: ME,
    },
  }),
  cluster("stg-us-east-1", {
    name: "Staging US East",
    region: "us-east-1",
    lifecycle: "managing",
    heartbeatStatus: "degraded",
    heartbeatAgeSeconds: 95,
  }),
  cluster("onprem-lab", {
    name: "On-prem lab",
    providerPluginSlug: "k8s_native",
    region: "",
    lifecycle: "registered",
    heartbeatStatus: "never_seen",
    heartbeatAgeSeconds: null,
    capabilitiesProbedAt: null,
    managedAt: null,
    capabilities: {},
  }),
  cluster("gke-eu-west-4", {
    name: "GKE Europe",
    providerPluginSlug: "gke",
    region: "europe-west4",
    lifecycle: "error",
    isActive: false,
    heartbeatStatus: "offline",
    heartbeatAgeSeconds: 720,
    lastManagementError:
      "cert-manager webhook did not become ready within 120s; check the cert-manager namespace.",
  }),
];

const SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
const ARN =
  "arn:aws:eks:ap-southeast-4:123456789012:cluster/prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-name/nodegroup/general-purpose-arm64-graviton-spot-capacity-pool-0123456789abcdef";

/** A 64-char SHA for a name, a 200-char ARN and an unbroken URL in the failure. */
export const LONG_CLUSTER = cluster(
  "prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-slug",
  {
    name: SHA,
    region: "ap-southeast-4-melbourne-local-zone",
    ingressClass: "nginx-internal-with-a-very-long-ingress-class-name",
    endpoint:
      "https://very-long-endpoint-name-0123456789abcdef.gr7.ap-southeast-4.eks.amazonaws.com",
    lifecycle: "error",
    lastManagementError: `Management workflow failed: the platform could not reach ${ARN} at https://very-long-endpoint-name-0123456789abcdef.gr7.ap-southeast-4.eks.amazonaws.com/api/v1/namespaces/kube-system/pods?labelSelector=app%3Dcert-manager-webhook after 5 attempts.`,
  }
);

/** Sixty clusters, for numbered pages. */
export const FLEET: ClusterRow[] = Array.from({ length: 60 }, (_, i) => {
  const base = CLUSTERS[i % CLUSTERS.length];
  const n = String(i + 1).padStart(2, "0");
  return { ...base, id: `cl-fleet-${n}`, slug: `${base.slug}-${n}`, name: `${base.name} ${n}` };
});

const SORT_VALUE: Record<string, (c: ClusterRow) => string | number> = {
  name: (c) => c.name.toLowerCase(),
  slug: (c) => c.slug,
  status: (c) => c.lifecycle,
  provider: (c) => c.providerPluginSlug,
  region: (c) => c.region,
  live: (c) => c.heartbeatStatus ?? "never_seen",
  lastProbe: (c) => (c.capabilitiesProbedAt ? Date.parse(c.capabilitiesProbedAt) : 0),
};

/**
 * A stand-in for `astroliftClustersPage` in stories: the fixture fleet
 * filtered, sorted and sliced the way the server answers the list state.
 */
export function serveClusters(
  fleet: ClusterRow[],
  {
    filters,
    sort,
    page,
    pageSize,
  }: { filters: Record<string, string>; sort: SortState[]; page: number; pageSize: number }
): { rows: ClusterRow[]; totalCount: number } {
  const kept = fleet
    .filter(
      (c) =>
        (!filters.provider || c.providerPluginSlug === filters.provider) &&
        (!filters.status || c.lifecycle === filters.status) &&
        (!filters.live || (c.heartbeatStatus ?? "never_seen") === filters.live) &&
        (!filters.registeredBy || c.createdByUsername === ME)
    )
    .sort((a, b) => {
      for (const s of sort) {
        const value = SORT_VALUE[s.key];
        if (!value) continue;
        const [x, y] = [value(a), value(b)];
        if (x !== y) return (x < y ? -1 : 1) * (s.dir === "asc" ? 1 : -1);
      }
      return a.slug < b.slug ? -1 : 1;
    });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: kept.slice(start, start + pageSize), totalCount: kept.length };
}

export const ACTIONS: ClusterActions = {
  deleting: false,
  bringing: false,
  refreshing: false,
  onUnregister: resolved,
  onBring: resolved,
  onRefresh: resolved,
};

/** Everything but the list controller, which the story makes with useLocalListState. */
export function listProps(
  patch: Partial<Omit<ClustersListProps, "list">> = {}
): Omit<ClustersListProps, "list"> {
  return {
    ...ACTIONS,
    rows: CLUSTERS,
    totalCount: CLUSTERS.length,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    registerHref: "/clusters/new",
    ...patch,
  };
}

export const REGISTER: RegisterClusterPageProps = {
  cancelHref: "/clusters",
  plugins: [
    {
      id: "pl-eks",
      slug: "eks",
      name: "AWS EKS",
      version: "1.4.0",
      isEnabled: true,
      capabilitiesManifest: {},
    },
    {
      id: "pl-gke",
      slug: "gke",
      name: "Google GKE",
      version: "1.2.1",
      isEnabled: true,
      capabilitiesManifest: {},
    },
    {
      id: "pl-k8s",
      slug: "k8s_native",
      name: "Kubernetes",
      version: "2.0.0",
      isEnabled: true,
      capabilitiesManifest: {},
    },
  ],
  pluginSlug: "eks",
  onPluginSlugChange: noop,
  regions: [
    { id: "us-west-2", label: "US West (Oregon)", continent: "north_america" },
    { id: "us-east-1", label: "US East (N. Virginia)", continent: "north_america" },
    { id: "eu-west-1", label: "Europe (Ireland)", continent: "europe" },
  ],
  regionsLoading: false,
  registering: false,
  onRegister: async () => ({ ok: true }),
};

export function detailProps(patch: Partial<ClusterDetailProps> = {}): ClusterDetailProps {
  return {
    slug: "prd-us-west-2",
    cluster: CLUSTERS[0],
    loading: false,
    renderLiveStats: () => null,
    ...ACTIONS,
    ...patch,
  };
}

export const LIVE_STATS: ClusterLiveStatsProps = {
  appCount: 14,
  appLoading: false,
  metrics: {
    available: true,
    nodeCount: 6,
    podRunningRatio: 0.97,
    cpuUtilization: 0.74,
    memoryUtilization: 0.92,
    deploymentReadyRatio: 1,
  },
  metricsLoading: false,
};
