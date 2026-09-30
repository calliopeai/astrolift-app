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

export interface AppIdentityValues {
  name: string;
  description: string;
}

/**
 * The app's name and description, saved alone through UPDATE_APP (the
 * mutation the deploy strategy already uses). The data half of
 * AppIdentityView. The repository is shown, not edited: it is set when the
 * app is registered.
 */
export function useAppIdentity(app: AstroliftRegisteredApp) {
  const t = useTranslations("apps.settingsTab.identity");
  const handleVersionMismatch = useVersionMismatch();
  const [save, { loading: saving }] = useMutation<UpdateResp>(UPDATE_APP, {
    refetchQueries: [{ query: GET_APP, variables: { slug: app.slug } }],
    awaitRefetchQueries: true,
  });

  /** Rejects on a failed save, so the section keeps the edits. */
  async function onSave(values: AppIdentityValues): Promise<void> {
    const { data } = await save({
      variables: {
        input: {
          id: app.id,
          name: values.name.trim(),
          description: values.description.trim(),
          ifMatchVersion: app.version,
        },
      },
    });
    if (data?.updateApp.ok) {
      toast.success(t("toastSaved"));
      return;
    }
    // A stale write gets the helper's toast and refetch; either way the
    // section keeps the edits and shows the reason.
    handleVersionMismatch(data?.updateApp, { label: "app" });
    throw new Error(data?.updateApp.errors?.[0]?.message ?? t("toastFailed"));
  }

  return {
    name: app.name,
    description: app.description ?? "",
    slug: app.slug,
    sourceRepo: app.sourceRepo ?? "",
    sourceUrl: app.sourceUrl ?? "",
    defaultBranch: app.defaultBranch ?? "",
    saving,
    onSave,
  };
}
