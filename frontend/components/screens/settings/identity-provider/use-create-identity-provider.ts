"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { CREATE_IDENTITY_PROVIDER } from "@/graphql/identity/identity.mutations";
import { LIST_IDENTITY_PROVIDERS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftIdentityProvider,
  IdpKind,
  MutationResult,
} from "@/graphql/identity/identity.types";

export interface CreateIdentityProviderInput {
  kind: IdpKind;
  displayName: string | null;
  oidcDiscoveryUrl: string | null;
  metadataUrl: string | null;
  clientId: string | null;
  clientSecretRef: string | null;
  config: unknown;
  setActive: boolean;
}

/** The create mutation behind CreateIdentityProviderSheet. */
export function useCreateIdentityProvider() {
  const [createIdp, { loading: creating }] = useMutation<{
    createIdentityProvider: MutationResult<AstroliftIdentityProvider>;
  }>(CREATE_IDENTITY_PROVIDER, {
    refetchQueries: [{ query: LIST_IDENTITY_PROVIDERS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the provider was created; the sheet then closes. */
  async function onCreate(input: CreateIdentityProviderInput): Promise<boolean> {
    const { data } = await createIdp({ variables: { input } });
    if (data?.createIdentityProvider.ok) {
      toast.success(`Configured ${data.createIdentityProvider.data?.name ?? input.kind}`);
      return true;
    }
    toast.error(data?.createIdentityProvider.errors?.[0]?.message ?? "Failed");
    return false;
  }

  return { onCreate, creating };
}
