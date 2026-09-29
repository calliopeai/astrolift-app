/**
 * Curated open-weight models for the Deploy model sheet (#2040), and the GPU
 * memory fit check. Sizes are parameter counts; weights are served in bf16.
 */

export interface CatalogModel {
  id: string;
  label: string;
  paramsB: number;
}

export const MODEL_CATALOG: readonly CatalogModel[] = [
  { id: "Qwen/Qwen3-8B", label: "Qwen3 8B", paramsB: 8 },
  { id: "meta-llama/Llama-3.1-8B-Instruct", label: "Llama 3.1 8B Instruct", paramsB: 8 },
  { id: "mistralai/Mistral-7B-Instruct-v0.3", label: "Mistral 7B Instruct v0.3", paramsB: 7 },
  { id: "google/gemma-3-12b-it", label: "Gemma 3 12B", paramsB: 12 },
  { id: "Qwen/Qwen3-32B", label: "Qwen3 32B", paramsB: 32 },
  { id: "meta-llama/Llama-3.3-70B-Instruct", label: "Llama 3.3 70B Instruct", paramsB: 70 },
];

// bf16 weights (2 bytes a parameter) plus ~20% for the KV cache and activations
// at vLLM's default 0.9 memory utilisation.
export function neededGiB(paramsB: number): number {
  return Math.ceil(paramsB * 2 * 1.2);
}

interface GpuNode {
  gpus?: Record<string, number>;
  labels?: Record<string, string>;
}

export type Fit =
  | { kind: "fits"; perGpuGiB: number }
  | { kind: "tooSmall"; perGpuGiB: number; needGiB: number }
  | { kind: "noNode"; most: number }
  | { kind: "unknown" };

/**
 * Whether ``gpus`` GPUs on one node of the cluster can hold ``needGiB``.
 * Reads the probe's per-node GPU counts and the GPU feature discovery
 * ``nvidia.com/gpu.memory`` label (MiB per GPU). ``unknown`` when the cluster
 * was never probed or the label is missing: the deploy still goes ahead.
 */
export function gpuFit(capabilities: unknown, gpus: number, needGiB: number): Fit {
  const nodes = ((capabilities as { gpu?: { nodes?: GpuNode[] } } | null)?.gpu?.nodes ??
    []) as GpuNode[];
  if (nodes.length === 0) return { kind: "unknown" };
  const count = (n: GpuNode) => Object.values(n.gpus ?? {}).reduce((a, b) => a + Number(b), 0);
  const big = nodes.filter((n) => count(n) >= gpus);
  if (big.length === 0) return { kind: "noNode", most: Math.max(0, ...nodes.map(count)) };
  const mem = big.map((n) => Number(n.labels?.["nvidia.com/gpu.memory"] ?? 0)).filter((m) => m > 0);
  if (mem.length === 0) return { kind: "unknown" };
  const perGpuGiB = Math.floor(Math.max(...mem) / 1024);
  return perGpuGiB * gpus >= needGiB
    ? { kind: "fits", perGpuGiB }
    : { kind: "tooSmall", perGpuGiB, needGiB };
}

/** A managed-service name from a model id: ``Qwen/Qwen3-8B`` becomes ``qwen3-8b``. */
export function serviceNameFor(modelId: string): string {
  const base = modelId.split("/").pop() ?? modelId;
  return (
    base
      .toLowerCase()
      .replace(/[^a-z0-9-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "model"
  );
}
