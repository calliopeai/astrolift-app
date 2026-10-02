"use client";

import { useTranslations } from "next-intl";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCloneDefinition,
  useCreateWorkflowStage,
  useDeleteWorkflowStage,
  useManifestExport,
  useManifestImport,
  useManifestPreview,
  useReorderWorkflowStages,
  useUpdateWorkflowStage,
  useWorkflowDefinition,
  useWorkflowsEntitlement,
  useWorkflowStages,
} from "@/graphql/workflows/tiered.hooks";
import type {
  TieredMutationResult,
  WorkflowManifestPreview,
} from "@/graphql/workflows/tiered.types";

import { edgeFromDraft } from "./back-edge";

import type { StageDraft } from "./StageBuilder";
import { useAgentWorkloadOptions, useSkillOptions } from "./use-picker-options";

function reportResult(result: TieredMutationResult | null | undefined, fallback: string): boolean {
  if (result?.ok) return true;
  const errors = result?.errors ?? [];
  if (errors.length > 0) {
    for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
  } else {
    toast.error(fallback);
  }
  return false;
}

/**
 * One workflow definition's stages and every write on them, the TOML
 * manifest round trip, and the picker options the stage editors need. The
 * data half of StageBuilder.
 */
export function useStageBuilder(slug: string) {
  const t = useTranslations("workflowBounds");
  const router = useRouter();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? null;
  const { canCreate, canManage } = useWorkflowsEntitlement();

  const { definition, loading: defLoading } = useWorkflowDefinition(slug, orgId);
  const { stages, loading: stagesLoading, refetch: refetchStages } = useWorkflowStages(slug);
  const agentWorkloads = useAgentWorkloadOptions(orgId);
  const skills = useSkillOptions(orgId);

  const [busyGuid, setBusyGuid] = useState<string | null>(null);

  const [createStage, { loading: creating }] = useCreateWorkflowStage();
  const [updateStage] = useUpdateWorkflowStage();
  const [deleteStage] = useDeleteWorkflowStage();
  const [reorderStages] = useReorderWorkflowStages();
  const [cloneDefinition, { loading: cloning }] = useCloneDefinition();
  const [exportManifest, exportResult] = useManifestExport();
  const [previewManifest] = useManifestPreview();
  const [importManifest] = useManifestImport();

  const sorted = useMemo(() => [...stages].sort((a, b) => a.order - b.order), [stages]);

  const onSaveStage = useCallback(
    async (guid: string, draft: StageDraft) => {
      let backEdge: unknown;
      try {
        backEdge = edgeFromDraft(draft.backEdge, draft.backEdgeValueJson);
      } catch {
        toast.error(t("invalidCondition"));
        return;
      }
      setBusyGuid(guid);
      try {
        const { data } = await updateStage({
          variables: {
            stageGuid: guid,
            kind: draft.kind,
            role: draft.role.trim() !== "" ? draft.role.trim() : null,
            onFailure: draft.onFailure,
            timeoutSeconds: draft.timeoutSeconds,
            maxAttempts: draft.maxAttempts ?? 3,
            backEdge: backEdge ?? {},
            agentDefinitionGuid: draft.agentDefinitionGuid,
            agentRef: draft.agentRef.trim(),
            workflowRef: draft.workflowRef.trim(),
            environmentSpecSlug: draft.environmentSpecSlug.trim(),
            skillRefs: draft.skillRefs,
            fanOutCount: draft.fanOutCount,
            prompt: draft.prompt,
            outputKey: draft.outputKey.trim(),
            approvers: draft.approvers,
          },
        });
        if (reportResult(data?.updateWorkflowStage, "Failed to save the stage")) {
          toast.success("Stage saved");
          await refetchStages();
        }
      } finally {
        setBusyGuid(null);
      }
    },
    [updateStage, refetchStages, t]
  );

  const onDeleteStage = useCallback(
    async (guid: string) => {
      setBusyGuid(guid);
      try {
        const { data } = await deleteStage({ variables: { stageGuid: guid } });
        if (reportResult(data?.deleteWorkflowStage, "Failed to delete the stage")) {
          toast.success("Stage deleted");
          await refetchStages();
        }
      } finally {
        setBusyGuid(null);
      }
    },
    [deleteStage, refetchStages]
  );

  const onMoveStage = useCallback(
    async (index: number, dir: -1 | 1) => {
      const target = index + dir;
      if (target < 0 || target >= sorted.length) return;
      const guids = sorted.map((s) => s.guid);
      [guids[index], guids[target]] = [guids[target], guids[index]];
      const { data } = await reorderStages({
        variables: { definitionSlug: slug, stageGuids: guids },
      });
      if (reportResult(data?.reorderWorkflowStages, "Failed to reorder stages")) {
        await refetchStages();
      }
    },
    [sorted, reorderStages, slug, refetchStages]
  );

  /** Resolves true once the stage exists, so the add form can close. */
  const onCreateStage = useCallback(
    async (draft: StageDraft): Promise<boolean> => {
      let backEdge: unknown;
      try {
        backEdge = edgeFromDraft(draft.backEdge, draft.backEdgeValueJson);
      } catch {
        toast.error(t("invalidCondition"));
        return false;
      }
      const nextOrder = sorted.length > 0 ? Math.max(...sorted.map((s) => s.order)) + 1 : 0;
      const { data } = await createStage({
        variables: {
          workflowSlug: slug,
          kind: draft.kind,
          order: nextOrder,
          role: draft.role.trim() !== "" ? draft.role.trim() : null,
          onFailure: draft.onFailure,
          timeoutSeconds: draft.timeoutSeconds,
          maxAttempts: draft.maxAttempts ?? 3,
          backEdge: backEdge ?? {},
          agentDefinitionGuid: draft.agentDefinitionGuid,
          agentRef: draft.agentRef.trim() || null,
          workflowRef: draft.workflowRef.trim() || null,
          environmentSpecSlug: draft.environmentSpecSlug.trim() || null,
          skillRefs: draft.skillRefs,
          fanOutCount: draft.fanOutCount,
          prompt: draft.prompt.trim() !== "" ? draft.prompt : null,
          outputKey: draft.outputKey.trim() || null,
          approvers: draft.approvers.length > 0 ? draft.approvers : null,
        },
      });
      if (reportResult(data?.createWorkflowStage, "Failed to add the stage")) {
        toast.success("Stage added");
        await refetchStages();
        return true;
      }
      return false;
    },
    [createStage, slug, sorted, refetchStages, t]
  );

  const onClone = useCallback(async () => {
    const { data } = await cloneDefinition({ variables: { slug, orgId } });
    const res = data?.cloneWorkflowDefinition;
    if (res?.ok && res.slug) {
      toast.success("Template cloned", { description: res.slug });
      router.push(`/workflows/${res.slug}/builder`);
      return;
    }
    reportResult(res, "Failed to clone the template");
  }, [cloneDefinition, slug, orgId, router]);

  const loadManifest = useCallback(() => {
    void exportManifest({ variables: { definitionSlug: slug } });
  }, [exportManifest, slug]);

  const previewToml = async (toml: string) => {
    const { data } = await previewManifest({ variables: { toml } });
    return data?.previewWorkflowManifest ?? null;
  };

  /** Imports the TOML as a new definition and opens it; an invalid manifest comes back. */
  const importAsNew = async (toml: string): Promise<WorkflowManifestPreview | null> => {
    const { data } = await importManifest({ variables: { toml, preview: false, orgId } });
    const res = data?.importWorkflowManifest;
    if (res?.ok && res.createdSlug) {
      toast.success("Definition imported", { description: res.createdSlug });
      router.push(`/workflows/${res.createdSlug}/builder`);
      return null;
    }
    reportResult(res, "Import failed");
    return res?.manifest && !res.manifest.ok && res.manifest.error ? res.manifest : null;
  };

  return {
    slug,
    orgId,
    canCreate,
    canManage,
    definition,
    defLoading,
    stages: sorted,
    stagesLoading,
    busyGuid,
    creating,
    cloning,
    onSaveStage,
    onDeleteStage,
    onMoveStage,
    onCreateStage,
    onClone,
    manifest: {
      load: loadManifest,
      exported: exportResult.data?.exportWorkflowManifest ?? null,
      loading: exportResult.loading || !exportResult.called,
      error: exportResult.error?.message ?? null,
      preview: previewToml,
      importAsNew,
    },
    pickerOptions: {
      workloads: agentWorkloads.workloads,
      workloadsLoading: agentWorkloads.loading,
      skills: skills.options,
      skillsLoading: skills.loading,
    },
  };
}
