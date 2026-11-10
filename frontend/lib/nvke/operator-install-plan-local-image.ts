import { z } from "zod";
import type { NvkeInstallPlan } from "./operator-install-plan";
import type { NvkeInstallPlanCharts } from "./operator-install-plan-charts";
import type { NvkeInstallPlanTree } from "./operator-install-plan-tree";
import {
  installPlanImageFormSchema,
  installPlanImageMetadataSchema,
  installPlanImageSource,
  validateInstallPlanImageSource,
} from "./operator-install-plan-image";
const pinned = /@(?:sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$/;
export const installPlanLocalImageFormSchema = installPlanImageFormSchema
  .omit({ policy: true, maximumCalls: true })
  .extend({
    reference: installPlanImageFormSchema.shape.reference.refine(
      (value) => value.split("@").length === 2 && pinned.test(value)
    ),
  })
  .strict();
export type NvkeInstallPlanLocalImageForm = z.infer<typeof installPlanLocalImageFormSchema>;
export const installPlanLocalImageSchema = installPlanImageMetadataSchema.safeExtend({
  sourceKind: z.literal("local"),
});
export type NvkeInstallPlanLocalImage = z.infer<typeof installPlanLocalImageSchema>;
export function installPlanLocalImageRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree
) {
  const form = installPlanLocalImageFormSchema.parse(values);
  const { plan, charts, tree } = installPlanImageSource(parent, publication, execution);
  if (new Set([form.artifactId, tree.id, charts.id, plan.id, plan.operationId]).size !== 5)
    throw new Error("INVALID_IMAGE_IDENTITY");
  return {
    format: 1 as const,
    sourceKind: "local" as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    reference: form.reference,
    maximumBytes: form.maximumBytes,
    timeoutSeconds: Number(form.timeoutSeconds),
  };
}
export function validateInstallPlanLocalImage(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  artifactId: string,
  limits?: Pick<NvkeInstallPlanLocalImageForm, "maximumBytes" | "reference">
) {
  const row = installPlanLocalImageSchema.parse(value);
  validateInstallPlanImageSource(row, parent, publication, execution, artifactId);
  if (limits) {
    const selected = installPlanLocalImageFormSchema
      .pick({ maximumBytes: true, reference: true })
      .parse({ maximumBytes: limits.maximumBytes, reference: limits.reference });
    if (BigInt(row.bytes) > BigInt(selected.maximumBytes)) throw new Error("IMAGE_BUDGET_EXCEEDED");
    if (row.rootDigest !== selected.reference.split("@")[1]) throw new Error("IMAGE_ROOT_CHANGED");
  }
  return row;
}
