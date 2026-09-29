"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { UPDATE_MY_PROFILE } from "@/graphql/identity/identity.mutations";
import { GET_MY_PROFILE } from "@/graphql/identity/identity.queries";
import type { AstroliftMyProfile, MutationResult } from "@/graphql/identity/identity.types";

interface ProfileResp {
  astroliftMyProfile: AstroliftMyProfile | null;
}
interface MutationResp {
  updateMyProfile: MutationResult<AstroliftMyProfile>;
}

// Build the sorted IANA timezone list once at module load — it's
// 400–600 entries depending on the browser; memoising avoids a
// new array on every render.
const TIMEZONES: string[] = (() => {
  try {
    return [
      ...(Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf!("timeZone"),
    ].sort();
  } catch {
    return [];
  }
})();

/** Settings › Profile › Timezone: the saved override and its update. */
export function useProfileTimezone() {
  const { data } = useQuery<ProfileResp>(GET_MY_PROFILE);
  const [updateProfile, { loading: saving }] = useMutation<MutationResp>(UPDATE_MY_PROFILE, {
    refetchQueries: [{ query: GET_MY_PROFILE }],
    awaitRefetchQueries: true,
  });

  const profile = data?.astroliftMyProfile ?? null;

  // Detect the browser's current timezone for the placeholder / default.
  const browserTz = React.useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, []);

  // savedTz is the persisted server value (null/empty = no override).
  const savedTz = profile?.timezone ?? null;

  /** Persists the picked zone ("" clears the override); true when saved. */
  async function save(picked: string): Promise<boolean> {
    const { data: res } = await updateProfile({
      variables: { input: { timezone: picked } },
    });
    if (res?.updateMyProfile.ok) {
      // Update the tz cookie immediately so the next server render
      // uses the new zone without waiting for a full page reload.
      if (typeof document !== "undefined") {
        const zone = picked || browserTz;
        document.cookie = `tz=${encodeURIComponent(zone)};path=/;max-age=31536000;SameSite=Lax`;
      }
      toast.success("Timezone saved");
      return true;
    }
    toast.error(res?.updateMyProfile.errors[0]?.message ?? "Failed to save timezone");
    return false;
  }

  return { timezones: TIMEZONES, browserTz, savedTz, saving, save };
}
