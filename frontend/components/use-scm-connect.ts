"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { CONNECT_USER_SOURCE_PROVIDER } from "@/graphql/identity/identity.mutations";
import { LIST_MY_CONNECTED_ACCOUNTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftConnectUserSourceProviderPayload,
  AstroliftMyConnectedAccount,
  MutationResult,
} from "@/graphql/identity/identity.types";

/** The viewer's source-provider identities and the connect flow: the data half of ScmConnectPrompt. */
interface ListResp {
  astroliftMyConnectedAccounts: AstroliftMyConnectedAccount[];
}

interface ConnectResp {
  astroliftConnectUserSourceProvider: MutationResult<AstroliftConnectUserSourceProviderPayload>;
}

/**
 * Provider "family" (github / gitlab / …) shared between a config kind
 * and the user-token kind it mints, so a failing `github_oauth_user`
 * maps back to its `github_oauth_app` / `github_app_install` config.
 */
export function providerFamily(kind: string): string {
  return kind.split("_")[0] ?? "";
}

export interface UseScmConnect {
  accounts: AstroliftMyConnectedAccount[];
  loading: boolean;
  starting: boolean;
  startConnect: (providerConfigId: string) => Promise<void>;
}

/**
 * Loads the viewer's connectable provider configs and exposes a
 * `startConnect` that kicks off the OAuth dance and hands the browser
 * off to the authorize URL, asking the host to return to the current
 * path when it's done.
 */
export function useScmConnect(): UseScmConnect {
  const pathname = usePathname();
  const { data, loading } = useQuery<ListResp>(LIST_MY_CONNECTED_ACCOUNTS, {
    fetchPolicy: "cache-and-network",
  });
  const [connect, connectState] = useMutation<ConnectResp>(CONNECT_USER_SOURCE_PROVIDER);

  const startConnect = React.useCallback(
    async (providerConfigId: string) => {
      try {
        const { data } = await connect({
          variables: { input: { providerConfigId, returnTo: pathname } },
        });
        const payload = data?.astroliftConnectUserSourceProvider;
        if (!payload?.ok || !payload.data) {
          toast.error(payload?.errors?.[0]?.message ?? "Could not start the reconnect flow");
          return;
        }
        // Full-page handoff to the OAuth start endpoint. The host bounces
        // back to `returnTo` (this wizard) once the token is refreshed.
        window.location.assign(payload.data.authorizationUrl);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Reconnect failed");
      }
    },
    [connect, pathname]
  );

  return {
    accounts: data?.astroliftMyConnectedAccounts ?? [],
    loading,
    starting: connectState.loading,
    startConnect,
  };
}
