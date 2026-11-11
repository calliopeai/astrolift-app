import { z } from "zod";
import type { NvkeInstallPlan } from "./operator-install-plan";
import type { NvkeInstallPlanCharts } from "./operator-install-plan-charts";
import type { NvkeInstallPlanTree } from "./operator-install-plan-tree";
import type { NvkeInstallPlanWorkload } from "./operator-install-plan-workload";
import { installPlanWorkloadMetadataSchema } from "./operator-install-plan-workload";
import type { NvkeInstallPlanImageBinding } from "./operator-install-plan-image-binding";
import type { NvkeInstallPlanPullAuthority } from "./operator-install-plan-pull-authority";
import {
  installPlanPullSecretAnalysisSchema,
  installPlanPullSecretAnalysisSource,
  validateInstallPlanPullSecretAnalysis,
  type NvkeInstallPlanPullSecretAnalysis,
} from "./operator-install-plan-pull-secret-analysis";
import {
  installPlanNodePlatformsSchema,
  validateInstallPlanNodePlatforms,
  type NvkeInstallPlanNodePlatforms,
} from "./operator-install-plan-node-platforms";

export const PULL_RUNTIME_STATE =
  "requires-workload-ownership-scheduling-node-keyring-plugins-registry-availability-fresh-use-and-linked-admission";

export const installPlanPullRuntimeSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanPullRuntimeType").optional(),
    analysis: installPlanPullSecretAnalysisSchema,
    nodePlatforms: installPlanNodePlatformsSchema,
    correspondenceDigest: z.string().regex(/^[0-9a-f]{64}$/),
    imageUseCount: z.number().int().min(1).max(1024),
    nodeCount: z.number().int().min(1).max(1024),
    sourceObservedAt: installPlanWorkloadMetadataSchema.shape.observedAt,
    nodeObservedAt: installPlanWorkloadMetadataSchema.shape.observedAt,
    executionState: z.literal(PULL_RUNTIME_STATE),
    continuationAuthorized: z.literal(false),
  })
  .strict();
export type NvkeInstallPlanPullRuntime = z.infer<typeof installPlanPullRuntimeSchema>;

export function installPlanPullRuntimeSource(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  observation: NvkeInstallPlanPullAuthority,
  candidates: NvkeInstallPlanPullSecretAnalysis,
  platforms: NvkeInstallPlanNodePlatforms
) {
  const source = installPlanPullSecretAnalysisSource(
    parent,
    publication,
    execution,
    inventory,
    images,
    observation
  );
  const analysis = validateInstallPlanPullSecretAnalysis(
    candidates,
    parent,
    publication,
    execution,
    inventory,
    images,
    observation,
    candidates.id
  );
  const nodePlatforms = validateInstallPlanNodePlatforms(
    platforms,
    parent,
    publication,
    execution,
    inventory,
    images,
    platforms.id
  );
  if (analysis.id === nodePlatforms.id) throw new Error("PULL_RUNTIME_SOURCE_CHANGED");
  return { ...source, analysis, nodePlatforms };
}

function sameCapture(actual: Record<string, unknown>, expected: Record<string, unknown>) {
  return Object.keys(expected).every(
    (key) => key === "__typename" || actual[key] === expected[key]
  );
}

export function validateInstallPlanPullRuntime(
  value: unknown,
  source: ReturnType<typeof installPlanPullRuntimeSource>
) {
  const result = installPlanPullRuntimeSchema.parse(value);
  if (
    !sameCapture(result.analysis, source.analysis) ||
    !sameCapture(result.nodePlatforms, source.nodePlatforms) ||
    result.imageUseCount !== source.analysis.imageUseCount ||
    result.nodeCount > Math.min(result.imageUseCount, source.nodePlatforms.nodeCount) ||
    result.sourceObservedAt !== source.analysis.sourceObservedAt ||
    Date.parse(result.nodeObservedAt) < Date.parse(source.binding.observedAt) ||
    Date.parse(result.nodeObservedAt) > Date.parse(source.nodePlatforms.observedAt)
  )
    throw new Error("PULL_RUNTIME_SOURCE_CHANGED");
  return result;
}
