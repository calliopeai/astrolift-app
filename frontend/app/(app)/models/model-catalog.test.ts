import { describe, expect, it } from "vitest";

import { gpuFit, neededGiB, serviceNameFor } from "./model-catalog";

const caps = {
  gpu: {
    nodes: [
      { gpus: { "nvidia.com/gpu": 1 }, labels: { "nvidia.com/gpu.memory": "23034" } },
      { gpus: { "nvidia.com/gpu": 4 }, labels: { "nvidia.com/gpu.memory": "81920" } },
    ],
  },
};

describe("gpuFit", () => {
  it("fits an 8B model on one 24 GB GPU and a 70B model on four 80 GB GPUs", () => {
    expect(gpuFit(caps, 1, neededGiB(8)).kind).toBe("fits");
    expect(gpuFit(caps, 4, neededGiB(70)).kind).toBe("fits");
  });

  it("refuses a 70B model on one GPU and asks for more GPUs than any node has", () => {
    expect(gpuFit(caps, 1, neededGiB(70))).toEqual({
      kind: "tooSmall",
      perGpuGiB: 80,
      needGiB: 168,
    });
    expect(gpuFit(caps, 8, neededGiB(8))).toEqual({ kind: "noNode", most: 4 });
  });

  it("is unknown for an unprobed cluster or one without GPU memory labels", () => {
    expect(gpuFit({}, 1, 10).kind).toBe("unknown");
    expect(gpuFit({ gpu: { nodes: [{ gpus: { "nvidia.com/gpu": 1 } }] } }, 1, 10).kind).toBe(
      "unknown"
    );
  });
});

it("names a service after the model", () => {
  expect(serviceNameFor("Qwen/Qwen3-8B")).toBe("qwen3-8b");
  expect(serviceNameFor("meta-llama/Llama-3.1-8B-Instruct")).toBe("llama-3-1-8b-instruct");
});
