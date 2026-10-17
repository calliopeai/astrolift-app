"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import type {
  ActionSafetyQuery,
  ArchiveBrainMutation,
  ArchiveBrainMutationVariables,
  RebuildBrainMutation,
  RebuildBrainMutationVariables,
} from "@/graphql/__generated__/operations";
import { ACTION_SAFETY } from "@/graphql/permissions/astrolift.queries";
import { ARCHIVE_BRAIN, REBUILD_BRAIN } from "@/graphql/registry/brains.mutations";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { gatedWrite } from "../gated-write";
import { environmentNames, gateAsk, pickEnvironment } from "./brain-detail-model";
import { activeBrainSection, brainSections } from "./brain-tabs-model";
import type { BrainRebuild, BrainSettingsTabProps } from "./BrainSettingsTab";
import type { FramedBrainValue } from "./use-brain-frame";

type Refusal = { message: string; approval: boolean } | null;

/**
 * The data half of the Settings tab (#2427): rebuildBrain (a production
 * deploy asks for a fresh sign-in; the prompt opens and the call is sent
 * once more) and archiveBrain (brain.delete: confirmation, a fresh sign-in
 * and any approval rule), with what brain.delete asks here. Once archived,
 * the layer no longer reads as one, and the frame offers Restore.
 */
export function useBrainSettings(framed: FramedBrainValue): BrainSettingsTabProps {
  const t = useTranslations("brains.settings");
  const { brain, can } = framed;
  const params = useSearchParams();
  const section = activeBrainSection("settings", Object.fromEntries(params?.entries() ?? [])) as
    | "rebuild"
    | "danger-zone";

  const environments = environmentNames(brain);
  const [picked, setPicked] = React.useState<string | null>(null);
  const environment = pickEnvironment(brain, picked);
  const [ingest, setIngest] = React.useState(false);
  const [result, setResult] = React.useState<BrainRebuild | null>(null);
  const [rebuildError, setRebuildError] = React.useState<Refusal>(null);
  const [rebuild, rebuildState] = useMutation<RebuildBrainMutation, RebuildBrainMutationVariables>(
    REBUILD_BRAIN
  );

  const perms = useMyPermissions();
  const safety = useQuery<ActionSafetyQuery>(ACTION_SAFETY, {
    variables: { appSlug: brain.slug },
    skip: section !== "danger-zone" || !can.archive || perms.loading || !perms.can("org.read"),
    fetchPolicy: "cache-first",
  });
  const [archive, archiveState] = useMutation<ArchiveBrainMutation, ArchiveBrainMutationVariables>(
    ARCHIVE_BRAIN
  );
  const [archiveError, setArchiveError] = React.useState<Refusal>(null);

  return {
    layerName: brain.name || brain.slug,
    section,
    sections: brainSections(brain.slug, "settings", (key) => t(`sectionLabels.${key}`)),
    canRebuild: can.rebuild,
    canArchive: can.archive,
    rebuild: {
      environments,
      environment,
      onEnvironment: setPicked,
      ingest,
      onIngest: setIngest,
      submitting: rebuildState.loading,
      onRebuild: async () => {
        setRebuildError(null);
        const outcome = await gatedWrite(
          async () =>
            (
              await rebuild({
                variables: { input: { slug: brain.slug, environmentName: environment, ingest } },
              })
            ).data?.rebuildBrain,
          t("rebuildFailed")
        );
        if (!outcome.ok) {
          setRebuildError({
            message: outcome.message,
            approval: outcome.code === "APPROVAL_REQUIRED",
          });
          return;
        }
        setResult(outcome.data);
        toast.success(ingest ? t("ingestStarted") : t("rebuildStarted"));
      },
      result,
      error: rebuildError,
    },
    archive: {
      ask: gateAsk(safety.data?.astroliftActionSafety, "brain.delete"),
      submitting: archiveState.loading,
      onArchive: async () => {
        setArchiveError(null);
        const outcome = await gatedWrite(
          async () =>
            (await archive({ variables: { input: { slug: brain.slug } } })).data?.archiveBrain,
          t("archiveFailed")
        );
        if (!outcome.ok) {
          setArchiveError({
            message: outcome.message,
            approval: outcome.code === "APPROVAL_REQUIRED",
          });
          return false;
        }
        toast.success(t("archived", { slug: brain.slug }));
        // The layer no longer reads as one: the frame turns to its archived state.
        await framed.refetch().catch(() => {});
        return true;
      },
      error: archiveError,
    },
  };
}
