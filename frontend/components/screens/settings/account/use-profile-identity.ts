"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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

export interface ProfileIdentityInput {
  firstName?: string;
  lastName?: string;
  email?: string;
}

/** Settings › Profile › Identity: the person's profile and its update. */
export function useProfileIdentity() {
  const { data, loading } = useQuery<ProfileResp>(GET_MY_PROFILE);
  const [updateProfile, { loading: saving }] = useMutation<MutationResp>(UPDATE_MY_PROFILE, {
    refetchQueries: [{ query: GET_MY_PROFILE }],
    awaitRefetchQueries: true,
  });

  /** Saves the changed fields; true when the server accepted them. */
  async function save(input: ProfileIdentityInput): Promise<boolean> {
    const { data } = await updateProfile({ variables: { input } });
    if (data?.updateMyProfile.ok) {
      toast.success("Profile updated");
      return true;
    }
    toast.error(data?.updateMyProfile.errors[0]?.message ?? "Update failed");
    return false;
  }

  return { profile: data?.astroliftMyProfile ?? null, loading, saving, save };
}
