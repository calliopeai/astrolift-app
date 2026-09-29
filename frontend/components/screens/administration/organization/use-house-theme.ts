"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { AstroliftOrganization, MutationResult } from "@/graphql/identity/identity.types";

/** The server half of HouseThemeCard (#135): saves the org's house theme. */
export function useHouseTheme(org: AstroliftOrganization) {
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
      toast.success(locked ? "House theme saved and locked" : "House theme saved");
      return true;
    }
    toast.error(data?.updateOrganization.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  return { org, saving, onSave };
}
