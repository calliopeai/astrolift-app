import type { ModelObservationState } from "@/graphql/__generated__/schema";
import type { ModelObservationsPanelProps, ModelObservation } from "./ModelObservationsPanel";
const observedAt = "2026-09-30T15:30:00Z";
export const observation = (
  key: string,
  unit: string,
  value: number | null,
  state: ModelObservationState = "AVAILABLE"
): ModelObservation => ({
  key,
  unit,
  value,
  state,
  source: "vllm",
  observedAt: state === "AVAILABLE" ? observedAt : null,
  aggregationWindowSeconds: key.endsWith("per_second") || key.endsWith("p95") ? 300 : 0,
  samples:
    value === null
      ? []
      : [
          { timestamp: "2026-09-30T15:29:00Z", value: value * 0.8 },
          { timestamp: observedAt, value },
        ],
});
const resources = {
  source: "managed_service_desired",
  observedAt,
  replicas: 1,
  cpuCoresPerReplica: 2,
  memoryBytesPerReplica: 8589934592,
  gpuDevicesPerReplica: 1,
  gpuResource: "nvidia.com/gpu",
  totalCpuCores: 2,
  totalMemoryBytes: 8589934592,
  totalGpuDevices: 1,
};
export const modelObservationsProps: ModelObservationsPanelProps = {
  clusterId: "cluster-one",
  clusterName: "Production",
  organizationId: "org",
  onRefresh: () => {},
  metrics: {
    loading: false,
    stale: false,
    error: null,
    data: {
      serviceId: "shared-model",
      clusterId: "cluster-one",
      start: "2026-09-30T15:15:00Z",
      end: observedAt,
      retrievedAt: observedAt,
      stepSeconds: 30,
      scope: "deployment_aggregate_not_app_attributed",
      sampleLimit: 120,
      metrics: [
        observation("prompt_tokens_per_second", "tokens/s", 42),
        observation("generation_tokens_per_second", "tokens/s", 25),
        observation("successful_requests_per_second", "requests/s", 1),
        observation("requests_running", "requests", 2),
        observation("requests_waiting", "requests", 0),
        observation("ttft_p95", "seconds", 0.8),
        observation("latency_p95", "seconds", 3.5),
        observation("queue_time_p95", "seconds", 0.2),
        observation("kv_cache_usage", "fraction", 0.25),
        observation("running_replicas", "replicas", 1),
        observation("ready_replicas", "replicas", 1),
        observation("cpu_usage", "cores", 0.4),
        observation("memory_usage", "bytes", 1000000),
        observation("cpu_requests", "cores", 2),
        observation("memory_requests", "bytes", 8589934592),
        observation("gpu_utilization", "fraction", null, "UNSUPPORTED"),
        observation("vram_usage", "bytes", null, "UNSUPPORTED"),
      ],
    },
  },
  density: {
    loading: false,
    stale: false,
    error: null,
    data: {
      clusterId: "cluster-one",
      start: "2026-09-30T15:15:00Z",
      end: observedAt,
      retrievedAt: observedAt,
      modelCount: 1,
      returnedCount: 1,
      inventoryLimit: 20,
      truncated: false,
      scope: "organization_cluster_owned_models",
      source: "managed_service_inventory",
      capacity: {
        state: "UNSUPPORTED",
        source: "unsupported_tenant_node_pool_mapping",
        observedAt: null,
        cpuCores: null,
        memoryBytes: null,
        vramBytes: null,
        freshnessSeconds: 1800,
        gpuDevices: [],
      },
      items: [
        {
          serviceId: "shared-model",
          name: "Qwen production",
          status: "active",
          desired: resources,
          applied: { ...resources, source: "managed_service_applied" },
          observations: [
            observation("running_replicas", "replicas", 1),
            observation("ready_replicas", "replicas", 1),
            observation("cpu_usage", "cores", 0.4),
            observation("memory_usage", "bytes", 1000000),
            observation("cpu_requests", "cores", 2),
            observation("memory_requests", "bytes", 8589934592),
          ],
        },
      ],
    },
  },
};
