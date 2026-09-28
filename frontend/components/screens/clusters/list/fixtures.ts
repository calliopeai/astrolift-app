import { fakeController } from "@/components/data-table/fixtures";
import type { CursorTableController } from "@/components/data-table";

import type { ClusterDetailProps } from "./ClusterDetail";
import type { ClusterLiveStatsProps } from "./ClusterLiveStats";
import type { ClustersListProps } from "./ClustersList";
import type { RegisterClusterSheetProps } from "./RegisterClusterSheet";
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

export const CLUSTERS: ClusterRow[] = [
  cluster("prd-us-west-2", { name: "Production US West" }),
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

export const LONG_CLUSTER = cluster(
  "prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-slug",
  {
    name: "Production US West 2 tenant cluster for shared workloads with a deliberately long display name",
    region: "ap-southeast-4-melbourne-local-zone",
    ingressClass: "nginx-internal-with-a-very-long-ingress-class-name",
    lifecycle: "error",
    lastManagementError:
      "Management workflow failed: the platform could not reach the cluster API server at https://very-long-endpoint-name.region.eks.amazonaws.com after 5 attempts; verify the security group allows the control plane egress range and that the kubeconfig context is current.",
  }
);

export function listProps(
  controller: Partial<CursorTableController<ClusterRow>> = {}
): ClustersListProps {
  return {
    table: fakeController<ClusterRow>({
      rows: CLUSTERS,
      totalCount: CLUSTERS.length,
      ...controller,
    }),
    deleting: false,
    bringing: false,
    refreshing: false,
    onUnregister: resolved,
    onBring: resolved,
    onRefresh: resolved,
    renderRegisterDialog: () => null,
  };
}

export const REGISTER: Omit<RegisterClusterSheetProps, "open" | "onOpenChange"> = {
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
  onRegister: async () => true,
};

export function detailProps(patch: Partial<ClusterDetailProps> = {}): ClusterDetailProps {
  return {
    slug: "prd-us-west-2",
    cluster: CLUSTERS[0],
    loading: false,
    renderLiveStats: () => null,
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
