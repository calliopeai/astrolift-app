"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import {
  SET_ACTIVE_IDENTITY_PROVIDER,
  SOFT_DELETE_IDENTITY_PROVIDER,
} from "@/graphql/identity/identity.mutations";
import { LIST_IDENTITY_PROVIDERS } from "@/graphql/identity/identity.queries";
import type { AstroliftIdentityProvider, MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface Resp {
  astroliftIdentityProviders: AstroliftIdentityProvider[];
}

/**
 * The org's identity providers and the switch-active / soft-delete
 * mutations behind their row actions. The data half of
 * IdentityProvidersScreen.
 */
export function useIdentityProviders() {
  const { can } = useMyPermissions();
  const canManageIdp = can("org.update");
  const { data, loading, error } = useQuery<Resp>(LIST_IDENTITY_PROVIDERS);

  const [setActive, { loading: switching }] = useMutation<{
    setActiveIdentityProvider: MutationResult<AstroliftIdentityProvider>;
  }>(SET_ACTIVE_IDENTITY_PROVIDER, {
    refetchQueries: [{ query: LIST_IDENTITY_PROVIDERS }],
    awaitRefetchQueries: true,
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteIdentityProvider: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_IDENTITY_PROVIDER, {
    refetchQueries: [{ query: LIST_IDENTITY_PROVIDERS }],
    awaitRefetchQueries: true,
  });

  /** Throws on failure so the confirm dialog stays open with the error. */
  async function onSetActive(idp: AstroliftIdentityProvider): Promise<void> {
    const { data } = await setActive({ variables: { input: { id: idp.id } } });
    if (data?.setActiveIdentityProvider.ok) {
      toast.success(`Active provider: ${idp.name}`);
    } else {
      throw new Error(data?.setActiveIdentityProvider.errors?.[0]?.message ?? "Failed");
    }
  }

  /** Throws on failure so the confirm dialog stays open with the error. */
  async function onDelete(idp: AstroliftIdentityProvider): Promise<void> {
    const { data } = await softDelete({ variables: { input: { id: idp.id } } });
    if (data?.softDeleteIdentityProvider.ok) {
      toast.success(`Deleted ${idp.name}`);
    } else {
      throw new Error(data?.softDeleteIdentityProvider.errors?.[0]?.message ?? "Failed");
    }
  }

  function onDeleteActiveBlocked() {
    toast.error("Cannot delete the active provider — set a different one first.");
  }

  return {
    providers: data?.astroliftIdentityProviders ?? [],
    loading,
    error,
    canManageIdp,
    switching,
    deleting,
    onSetActive,
    onDelete,
    onDeleteActiveBlocked,
  };
}
