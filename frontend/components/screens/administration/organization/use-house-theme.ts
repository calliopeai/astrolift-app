"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { AstroliftOrganization, MutationResult } from "@/graphql/identity/identity.types";

/** The server half of HouseThemeCard (#135): saves the org's house theme. */
export function useHouseTheme(org: AstroliftOrganization) {
  const t = useTranslations("adminHouseTheme");
  const [updateOrg, { loading: saving }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    refetchQueries: [{ query: LIST_ORGANIZATIONS }],
    awaitRefetchQueries: true,
  });

  async function onSave(
    appearanceDefault: Record<string, string>,
    locked: boolean
  ): Promise<boolean> {
    const { data } = await updateOrg({
      variables: {
        input: { id: org.id, appearanceDefault, appearanceLocked: locked },
      },
    });
    if (data?.updateOrganization.ok) {
      toast.success(locked ? t("savedLocked") : t("saved"));
      return true;
    }
    toast.error(data?.updateOrganization.errors?.[0]?.message ?? t("saveFailed"));
    return false;
  }

  return { org, saving, onSave };
}
