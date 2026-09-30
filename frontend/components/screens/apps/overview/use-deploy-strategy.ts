"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, TriggerMode } from "@/graphql/registry/registry.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useVersionMismatch } from "@/lib/apollo/use-version-mismatch";

interface UpdateResp {
  updateApp: MutationResult<Partial<AstroliftRegisteredApp>>;
}

export interface DeployStrategyValues {
  triggerMode: TriggerMode;
  deployBranch: string;
  previewEnabled: boolean;
  cronExpression: string;
}

/**
 * The app's deploy strategy and the UPDATE_APP save behind its edit
 * sheet. The data half of DeployStrategyCardView.
 */
export function useDeployStrategy(app: AstroliftRegisteredApp) {
  const t = useTranslations("apps.deployStrategy");
  const handleVersionMismatch = useVersionMismatch();
  const [save, { loading: saving }] = useMutation<UpdateResp>(UPDATE_APP, {
    refetchQueries: [{ query: GET_APP, variables: { slug: app.slug } }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the save landed (close the sheet). */
  async function onSave(values: DeployStrategyValues): Promise<boolean> {
    const { data } = await save({
      variables: {
        input: {
          id: app.id,
          triggerMode: values.triggerMode,
          deployBranch: values.deployBranch.trim() || null,
          previewEnabled: values.previewEnabled,
          // The backend ignores cron_expression unless mode == 'cron',
          // and requires it when mode == 'cron'. Send the trimmed value
          // straight through; validation lives server-side.
          cronExpression: values.triggerMode === "cron" ? values.cronExpression.trim() : null,
          // #497 — optimistic-concurrency guard. Passing the version
          // we cached when the sheet opened tells the backend to
          // refuse the write if a peer admin edited the app row
          // first; the helper below shows a toast and the GET_APP
          // refetch (refetchQueries above) reloads the latest state.
          ifMatchVersion: app.version,
        },
      },
    });
    if (data?.updateApp.ok) {
      toast.success(t("toastSaved"));
      return true;
    }
    if (handleVersionMismatch(data?.updateApp, { label: "app" })) {
      // Stale write — refetch already scheduled by ``refetchQueries``;
      // leave the sheet open so the operator can re-confirm on the
      // freshly loaded values.
      return false;
    }
    toast.error(data?.updateApp.errors?.[0]?.message ?? t("toastFailed"));
    return false;
  }

  return { app, saving, onSave };
}
