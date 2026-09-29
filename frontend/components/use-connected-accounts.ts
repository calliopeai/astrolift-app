"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import {
  CONNECT_USER_SOURCE_PROVIDER,
  DISCONNECT_USER_SOURCE_PROVIDER,
} from "@/graphql/identity/identity.mutations";
import { LIST_MY_CONNECTED_ACCOUNTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftConnectUserSourceProviderPayload,
  AstroliftDisconnectUserSourceProviderPayload,
  AstroliftMyConnectedAccount,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface ListResp {
  astroliftMyConnectedAccounts: AstroliftMyConnectedAccount[];
}
interface ConnectResp {
  astroliftConnectUserSourceProvider: MutationResult<AstroliftConnectUserSourceProviderPayload>;
}
interface DisconnectResp {
  astroliftDisconnectUserSourceProvider: MutationResult<AstroliftDisconnectUserSourceProviderPayload>;
}

/** The viewer's personal source-provider identities, with connect and disconnect (#395). */
export function useConnectedAccounts() {
  const { data, loading } = useQuery<ListResp>(LIST_MY_CONNECTED_ACCOUNTS, {
    fetchPolicy: "cache-and-network",
  });
  const [connect, connectState] = useMutation<ConnectResp>(CONNECT_USER_SOURCE_PROVIDER);
  const [disconnect, disconnectState] = useMutation<DisconnectResp>(
    DISCONNECT_USER_SOURCE_PROVIDER,
    {
      refetchQueries: [{ query: LIST_MY_CONNECTED_ACCOUNTS }],
      awaitRefetchQueries: true,
    }
  );

  async function onConnect(account: AstroliftMyConnectedAccount) {
    try {
      const { data } = await connect({
        variables: { input: { providerConfigId: account.providerConfigId } },
      });
      const payload = data?.astroliftConnectUserSourceProvider;
      if (!payload?.ok || !payload.data) {
        toast.error(payload?.errors?.[0]?.message ?? "Could not start the OAuth flow");
        return;
      }
      // Hand off to the OAuth start endpoint; the return picks up the new state.
      window.location.href = payload.data.authorizationUrl;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connect failed");
    }
  }

  /** Throws with the server's message on refusal, for ConfirmDialog to show. */
  async function onDisconnect(account: AstroliftMyConnectedAccount, confirmAccountLogin: string) {
    const { data } = await disconnect({
      variables: { input: { providerConfigId: account.providerConfigId, confirmAccountLogin } },
    });
    const payload = data?.astroliftDisconnectUserSourceProvider;
    if (!payload?.ok) {
      throw new Error(payload?.errors?.[0]?.message ?? "Could not disconnect this account");
    }
    toast.success(`Disconnected ${account.linkedAccountLogin ?? "account"}`);
  }

  return {
    accounts: data?.astroliftMyConnectedAccounts ?? [],
    loading,
    connecting: connectState.loading,
    disconnecting: disconnectState.loading,
    onConnect,
    onDisconnect,
  };
}
