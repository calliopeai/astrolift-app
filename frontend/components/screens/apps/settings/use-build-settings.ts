"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useVersionMismatch } from "@/lib/apollo/use-version-mismatch";

interface UpdateResp {
  updateApp: MutationResult<Partial<AstroliftRegisteredApp>>;
}

export interface BuildSettingsValues {
  buildContext: string;
  dockerfilePath: string;
}

export type BuildSettingsSource = Pick<
  AstroliftRegisteredApp,
  | "id"
  | "slug"
  | "version"
  | "buildContext"
  | "dockerfilePath"
  | "buildMode"
  | "manifestPath"
  | "sourceRepo"
>;

/**
 * The app's build context and Dockerfile, saved alone through UPDATE_APP
 * with the version the page read (astrolift-cli #159). The data half of
 * BuildSettingsView.
 */
export function useBuildSettings(app: BuildSettingsSource) {
  const t = useTranslations("apps.settingsTab.build");
  const handleVersionMismatch = useVersionMismatch();
  const [save, { loading: saving }] = useMutation<UpdateResp>(UPDATE_APP, {
    refetchQueries: [{ query: GET_APP, variables: { slug: app.slug } }],
    awaitRefetchQueries: true,
  });

  /** Rejects on a failed save, so the section keeps the edits. */
  async function onSave(values: BuildSettingsValues): Promise<void> {
    const { data } = await save({
      variables: {
        input: {
          id: app.id,
          buildContext: values.buildContext.trim(),
          dockerfilePath: values.dockerfilePath.trim(),
          ifMatchVersion: app.version,
        },
      },
    });
    if (data?.updateApp.ok) {
      toast.success(t("toastSaved"));
      return;
    }
    handleVersionMismatch(data?.updateApp, { label: "app" });
    throw new Error(data?.updateApp.errors?.[0]?.message ?? t("toastFailed"));
  }

  return {
    buildContext: app.buildContext ?? "",
    dockerfilePath: app.dockerfilePath ?? "",
    buildMode: app.buildMode ?? "",
    manifestPath: app.manifestPath ?? "",
    hasRepository: Boolean(app.sourceRepo),
    slug: app.slug,
    saving,
    onSave,
  };
}
