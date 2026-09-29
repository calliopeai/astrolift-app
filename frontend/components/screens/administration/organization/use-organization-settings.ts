"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { AstroliftOrganization, MutationResult } from "@/graphql/identity/identity.types";

export interface OrganizationSettingsDraft {
  name: string;
  website: string;
  auditDays: string;
  allowProfileEdit: boolean;
}

/** The server half of the Organization settings screen. */
export function useOrganizationSettings() {
  const { org, loading } = useActiveOrg();

  const [updateOrg, { loading: saving }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    refetchQueries: [{ query: LIST_ORGANIZATIONS }],
    awaitRefetchQueries: true,
  });

  async function onSave(draft: OrganizationSettingsDraft): Promise<boolean> {
    if (!org) return false;
    const { data } = await updateOrg({
      variables: {
        input: {
          id: org.id,
          name: draft.name.trim(),
          website: draft.website.trim(),
          auditLogRetentionDays: Number(draft.auditDays) || 365,
          allowUserProfileEdit: draft.allowProfileEdit,
        },
      },
    });
    if (data?.updateOrganization.ok) {
      toast.success("Organization updated");
      return true;
    }
    toast.error(data?.updateOrganization.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  return { org, loading, saving, onSave };
}
