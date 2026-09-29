"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { UPDATE_ORGANIZATION } from "@/graphql/identity/identity.mutations";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import type { AstroliftOrganization, MutationResult } from "@/graphql/identity/identity.types";
import { MY_UI_PREFERENCES } from "@/graphql/identity/ui-preferences.queries";
import type { RestrictedSettings } from "@/lib/display-prefs";

export interface OrganizationSettingsDraft {
  name: string;
  website: string;
  auditDays: string;
  allowProfileEdit: boolean;
  /** Settings a member can't change, for members who have not chosen themselves. */
  restrictedSettingsDefault: RestrictedSettings;
}

/** The server half of the Organization settings screen. */
export function useOrganizationSettings() {
  const { org, loading } = useActiveOrg();

  const [updateOrg, { loading: saving }] = useMutation<{
    updateOrganization: MutationResult<AstroliftOrganization>;
  }>(UPDATE_ORGANIZATION, {
    // The viewer's own preferences resolve against the org default, so they
    // are read again with it.
    refetchQueries: [{ query: LIST_ORGANIZATIONS }, { query: MY_UI_PREFERENCES }],
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
          restrictedSettingsDefault: draft.restrictedSettingsDefault,
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
