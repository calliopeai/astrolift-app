import type {
  AstroliftAppPod,
  AstroliftContainerStatus,
  AstroliftScheduledJobRun,
  AstroliftWorkloadPodStatusBucket,
} from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { ManifestCardViewProps } from "./ManifestCard";
import type { ResourceUsageGaugesViewProps } from "./ResourceUsageGauges";
import type { ScalePopoverViewProps } from "./ScalePopover";
import type { ScalingCardViewProps } from "./ScalingCard";
import type { K8sResource } from "./use-workload-manifest";
import type { ScalingStatus } from "./use-workload-scaling";
import type { WorkloadLiveStatus } from "./use-workloads-list";
import type { WorkloadDetailScreenProps } from "./WorkloadDetailScreen";
import type { WorkloadsListScreenProps } from "./WorkloadsListScreen";

/** Hand-typed fixtures for the app workloads list and workload detail screens. */

const resolved = async () => true;

export const LONG =
  "billing-reconciliation-nightly-export-for-the-finance-warehouse-with-a-deliberately-long-name";

const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const MIN = 60 * 1000;
const HOUR = 60 * MIN;

/** The generated JSON scalar makes `volumes` an odd intersection; cast the parsed-dict array. */
const volumes = (v: Record<string, unknown>[]) => v as unknown as AstroliftWorkload["volumes"];

export function workload(overrides: Partial<AstroliftWorkload> = {}): AstroliftWorkload {
  return {
    id: "wl-api",
    slug: "api",
    name: "API",
    kind: "deployment",
    registeredAppSlug: "billing",
    concurrencyPolicy: "",
    cpuLimit: "500m",
    cpuRequest: "100m",
    memoryLimit: "512Mi",
    memoryRequest: "128Mi",
    hpaMaxReplicas: null,
    hpaMinReplicas: null,
    hpaTargetCpuPct: 80,
    inClusterServiceFqdn: "api.billing.svc.cluster.local",
    isPublic: true,
    replicas: 3,
    schedule: "",
    storageClass: "",
    storageSize: "",
    volumes: volumes([]),
    ...overrides,
  };
}

// ── List ───────────────────────────────────────────────────────────────────

export const APP = { name: "Billing", slug: "billing", manifestPath: "astrolift.yaml" };

export const WORKLOADS: AstroliftWorkload[] = [
  workload(),
  workload({
    id: "wl-worker",
    slug: "worker",
    name: "Worker",
    isPublic: false,
    replicas: 2,
    hpaMinReplicas: 2,
    hpaMaxReplicas: 8,
    hpaTargetCpuPct: 70,
  }),
  workload({
    id: "wl-ledger-db",
    slug: "ledger-db",
    name: "Ledger DB",
    kind: "statefulset",
    isPublic: false,
    replicas: 1,
    cpuRequest: "",
    memoryRequest: "1Gi",
  }),
  workload({
    id: "wl-nightly",
    slug: "nightly-export",
    name: "Nightly export",
    kind: "cronjob",
    isPublic: false,
    replicas: 1,
    schedule: "0 2 * * *",
  }),
];

const LIVE: Map<string, WorkloadLiveStatus> = new Map([
  ["api", { ready: 3, desired: 3, maxRestarts: 0, errorEvent: null }],
  ["worker", { ready: 1, desired: 2, maxRestarts: 2, errorEvent: null }],
  [
    "ledger-db",
    {
      ready: 0,
      desired: 1,
      maxRestarts: 7,
      errorEvent: {
        reason: "CrashLoopBackOff",
        message: "Back-off restarting failed container ledger-db in pod ledger-db-0",
        count: 12,
        lastSeen: ago(2 * MIN),
      },
    },
  ],
]);

/** Everything but the list state, which stories build with `useLocalListState`. */
export type WorkloadsListFixture = Omit<WorkloadsListScreenProps, "list">;

export const LIST: WorkloadsListFixture = {
  rows: WORKLOADS,
  totalCount: WORKLOADS.length,
  loading: false,
  error: null,
  onRetry: () => {},
  liveStatus: LIVE,
  slug: "billing",
  basePath: "/apps",
};

export const LIST_EMPTY: WorkloadsListFixture = {
  ...LIST,
  rows: [],
  totalCount: 0,
  liveStatus: new Map(),
};

export const LIST_LONG: WorkloadsListFixture = {
  ...LIST,
  slug: LONG,
  rows: [
    workload({
      id: "wl-long",
      slug: LONG,
      name: LONG,
      cpuRequest: "1500m",
      memoryRequest: "8Gi",
      schedule: "*/5 * * * *",
    }),
    ...WORKLOADS,
  ],
  totalCount: WORKLOADS.length + 1,
  liveStatus: new Map([
    ...LIVE,
    [
      LONG,
      {
        ready: 0,
        desired: 3,
        maxRestarts: 2,
        errorEvent: {
          reason: "CreateContainerConfigError",
          message: `secret "${LONG}" not found`,
          count: 1,
          lastSeen: ago(MIN),
        },
      },
    ],
  ]),
};

export const SCALE: ScalePopoverViewProps = {
  workloadName: "API",
  currentDesired: 3,
  loading: false,
  apply: resolved,
};

// ── Detail ─────────────────────────────────────────────────────────────────

function containerStatus(
  overrides: Partial<AstroliftContainerStatus> = {}
): AstroliftContainerStatus {
  return {
    name: "api",
    image: "ghcr.io/acme/billing-api:1.42.0",
    kind: "primary",
    ready: true,
    restarts: 0,
    state: "running",
    terminatedReason: "",
    waitingReason: "",
    lastRestartAt: null,
    lastRestartReasons: [],
    resources: {
      cpuRequest: "100m",
      cpuLimit: "500m",
      memoryRequest: "128Mi",
      memoryLimit: "512Mi",
    },
    ...overrides,
  };
}

function pod(overrides: Partial<AstroliftAppPod> = {}): AstroliftAppPod {
  return {
    name: "api-7d9f8c6b5-x2kq9",
    workload: "api",
    node: "ip-10-0-12-34.ec2.internal",
    phase: "Running",
    status: "Running",
    ready: true,
    restarts: 0,
    age: ago(3 * HOUR),
    recentErrorEvent: null,
    containerStatuses: [
      containerStatus({
        name: "migrate",
        kind: "init",
        state: "terminated",
        terminatedReason: "Completed",
        ready: false,
      }),
      containerStatus(),
      containerStatus({
        name: "istio-proxy",
        kind: "sidecar",
        image: "docker.io/istio/proxyv2:1.22.1",
      }),
    ],
    ...overrides,
  };
}

export const PODS: AstroliftAppPod[] = [
  pod(),
  pod({ name: "api-7d9f8c6b5-m4ltz", age: ago(26 * HOUR) }),
  pod({
    name: "api-7d9f8c6b5-q8vwn",
    status: "CrashLoopBackOff",
    phase: "Running",
    ready: false,
    restarts: 9,
    age: ago(4 * 24 * HOUR),
    containerStatuses: [
      containerStatus({
        restarts: 9,
        ready: false,
        state: "waiting",
        waitingReason: "CrashLoopBackOff",
        lastRestartAt: ago(5 * MIN),
        lastRestartReasons: ["OOMKilled", "Error", "OOMKilled"],
      }),
    ],
  }),
];

export const BUCKETS: AstroliftWorkloadPodStatusBucket[] = [
  {
    status: "Running",
    count: 2,
    percent: 66.7,
    pods: [
      { name: "api-7d9f8c6b5-x2kq9", age: ago(3 * HOUR), ready: true },
      { name: "api-7d9f8c6b5-m4ltz", age: ago(26 * HOUR), ready: true },
    ],
  } as AstroliftWorkloadPodStatusBucket,
  {
    status: "CrashLoopBackOff",
    count: 1,
    percent: 33.3,
    pods: [{ name: "api-7d9f8c6b5-q8vwn", age: ago(4 * 24 * HOUR), ready: false }],
  } as AstroliftWorkloadPodStatusBucket,
];

export const CONTAINERS: AstroliftContainer[] = [
  {
    id: "c-api",
    name: "api",
    workloadSlug: "api",
    imageRef: "ghcr.io/acme/billing-api:1.42.0",
    isPrimary: true,
    port: 8080,
    healthcheckKind: "http",
    healthcheckPort: 8080,
    healthcheckValue: "/healthz",
    args: [],
    command: [],
    buildContext: ".",
    dockerfilePath: "Dockerfile",
    env: {},
    startupProbe: {
      httpGet: { path: "/healthz", port: 8080 },
      periodSeconds: 5,
      failureThreshold: 30,
    },
    readinessProbe: {
      httpGet: { path: "/ready", port: 8080 },
      initialDelaySeconds: 5,
      periodSeconds: 10,
    },
    livenessProbe: null,
  },
];

const VOLUMES = [
  {
    name: "ledger",
    kind: "pvc",
    mount_path: "/var/lib/ledger",
    size: "20Gi",
    storage_class: "gp3",
  },
  { name: "scratch", kind: "empty_dir", mount_path: "/tmp", size_limit: "1Gi" },
  { name: "config", kind: "config_map", mount_path: "/etc/billing", source_name: "billing-config" },
];

export const DETAIL: WorkloadDetailScreenProps = {
  workload: workload({ volumes: volumes(VOLUMES) }),
  workloadLoading: false,
  canDeploy: true,
  containers: CONTAINERS,
  containersLoading: false,
  pods: PODS,
  podsLoading: false,
  buckets: BUCKETS,
  bucketsLoading: false,
  liveContainers: PODS[0].containerStatuses,
  isCronjob: false,
  runs: [],
  runsLoading: false,
  appSlug: "billing",
  workloadSlug: "api",
  basePath: "/apps",
};

export const DETAIL_EMPTY: WorkloadDetailScreenProps = {
  ...DETAIL,
  workload: workload({ volumes: volumes([]), inClusterServiceFqdn: "" }),
  containers: [],
  pods: [],
  buckets: [],
  liveContainers: [],
};

export const RUNS: AstroliftScheduledJobRun[] = [
  {
    id: "run-0001-aaaa-bbbb",
    registeredAppSlug: "billing",
    workloadSlug: "nightly-export",
    environmentName: "production",
    status: "succeeded",
    k8sJobName: "nightly-export-29311020",
    startedAt: ago(10 * HOUR),
    endedAt: ago(10 * HOUR - 94_000),
    durationSeconds: 94,
    exitCode: 0,
    createdAt: ago(10 * HOUR),
    logExcerpt: "",
    output: "",
  },
  {
    id: "run-0002-cccc-dddd",
    registeredAppSlug: "billing",
    workloadSlug: "nightly-export",
    environmentName: "production",
    status: "failed",
    k8sJobName: "",
    startedAt: ago(34 * HOUR),
    endedAt: ago(34 * HOUR - 12_000),
    durationSeconds: 12,
    exitCode: 1,
    createdAt: ago(34 * HOUR),
    logExcerpt: "",
    output: "",
  },
];

export const DETAIL_CRONJOB: WorkloadDetailScreenProps = {
  ...DETAIL,
  workload: workload({
    id: "wl-nightly",
    slug: "nightly-export",
    name: "Nightly export",
    kind: "cronjob",
    isPublic: false,
    replicas: 1,
    schedule: "0 2 * * *",
  }),
  workloadSlug: "nightly-export",
  isCronjob: true,
  runs: RUNS,
};

export const DETAIL_LONG: WorkloadDetailScreenProps = {
  ...DETAIL,
  appSlug: LONG,
  workloadSlug: LONG,
  workload: workload({
    slug: LONG,
    name: LONG,
    inClusterServiceFqdn: `${LONG}.${LONG}.svc.cluster.local`,
    storageClass: LONG,
    storageSize: "500Gi",
    volumes: volumes([
      { name: LONG, kind: "secret", mount_path: `/etc/secrets/${LONG}`, source_name: LONG },
    ]),
  }),
  pods: [pod({ name: `${LONG}-7d9f8c6b5-x2kq9`, node: LONG, workload: LONG })],
  buckets: [
    {
      status: "CreateContainerConfigError",
      count: 1,
      percent: 100,
      pods: [{ name: `${LONG}-7d9f8c6b5-x2kq9`, age: ago(HOUR), ready: false }],
    } as AstroliftWorkloadPodStatusBucket,
  ],
  liveContainers: [containerStatus({ name: LONG, image: `ghcr.io/acme/${LONG}:${LONG}` })],
  containers: [{ ...CONTAINERS[0], name: LONG, imageRef: `ghcr.io/acme/${LONG}:${LONG}` }],
};

// ── Cards ──────────────────────────────────────────────────────────────────

export const USAGE: ResourceUsageGaugesViewProps = {
  loading: false,
  usage: {
    cpu: {
      unit: "cores",
      current: 0.42,
      request: 0.1,
      limit: 0.5,
      percentOfRequest: 420,
      percentOfLimit: 84,
    },
    memory: {
      unit: "bytes",
      current: 201 * 1024 * 1024,
      request: 128 * 1024 * 1024,
      limit: 512 * 1024 * 1024,
      percentOfRequest: 157,
      percentOfLimit: 39.3,
    },
    sourcedAt: ago(12_000),
  },
};

export const USAGE_OVER: ResourceUsageGaugesViewProps = {
  loading: false,
  usage: {
    cpu: {
      unit: "cores",
      current: 2.5,
      request: 1,
      limit: 0,
      percentOfRequest: 250,
      percentOfLimit: 0,
    },
    memory: {
      unit: "bytes",
      current: 3.2 * 1024 * 1024 * 1024,
      request: 1024 * 1024 * 1024,
      limit: 0,
      percentOfRequest: 320,
      percentOfLimit: 0,
    },
    sourcedAt: ago(4 * MIN),
  },
};

const SCALING_STATUS: ScalingStatus = {
  hpaEnabled: false,
  hpaMinReplicas: null,
  hpaMaxReplicas: null,
  hpaTargetCpuPct: 80,
  currentReplicas: 3,
  desiredReplicas: 3,
  isScaling: false,
  replicaLowerBound: 0,
  replicaUpperBound: 10,
  sourcedAt: ago(3_000),
};

export const SCALING: ScalingCardViewProps = {
  status: SCALING_STATUS,
  loading: false,
  scaling: false,
  canDeploy: true,
  onApply: resolved,
};

export const SCALING_HPA: ScalingCardViewProps = {
  ...SCALING,
  status: {
    ...SCALING_STATUS,
    hpaEnabled: true,
    hpaMinReplicas: 2,
    hpaMaxReplicas: 8,
    hpaTargetCpuPct: 70,
    currentReplicas: 7,
    desiredReplicas: 8,
    isScaling: true,
  },
};

const DEPLOYMENT: K8sResource = {
  apiVersion: "apps/v1",
  kind: "Deployment",
  metadata: { name: "api", namespace: "billing", labels: { app: "billing", workload: "api" } },
  spec: {
    replicas: 3,
    template: {
      spec: {
        containers: [
          {
            name: "api",
            image: "ghcr.io/acme/billing-api:1.42.0",
            ports: [{ containerPort: 8080 }],
          },
        ],
      },
    },
  },
};

const SERVICE: K8sResource = {
  apiVersion: "v1",
  kind: "Service",
  metadata: { name: "api", namespace: "billing" },
  spec: { ports: [{ port: 80, targetPort: 8080 }], selector: { workload: "api" } },
};

const MANIFEST_BASE = {
  appSlug: "billing",
  workloadSlug: "api",
  environmentName: "production",
  imageTag: "1.42.0",
  namespace: "billing",
  resources: [DEPLOYMENT, SERVICE],
  previousImageTag: "1.41.3",
  previousDeploymentId: "dep-1",
  resourcesPrevious: [
    {
      ...DEPLOYMENT,
      spec: {
        replicas: 2,
        template: {
          spec: {
            containers: [
              {
                name: "api",
                image: "ghcr.io/acme/billing-api:1.41.3",
                ports: [{ containerPort: 8080 }],
              },
            ],
          },
        },
      },
    },
    SERVICE,
  ],
  error: null,
  errorPath: null,
  errorLine: null,
  errorColumn: null,
};

export const MANIFEST: ManifestCardViewProps = { loading: false, manifest: MANIFEST_BASE };

export const MANIFEST_ERROR: ManifestCardViewProps = {
  loading: false,
  manifest: {
    ...MANIFEST_BASE,
    resources: [],
    previousImageTag: "",
    error: "workloads.api.containers[0].port: expected an integer, got 'http'",
    errorPath: "astrolift.yaml",
    errorLine: 14,
    errorColumn: 11,
  },
};

export const MANIFEST_EMPTY: ManifestCardViewProps = {
  loading: false,
  manifest: { ...MANIFEST_BASE, resources: [], resourcesPrevious: [], previousImageTag: "" },
};
