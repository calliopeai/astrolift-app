"use client";

/**
 * Inline SCM (re)connect affordances for the onboarding repo pickers.
 *
 * When a picker's source connection 401s (an expired per-user OAuth
 * token) or when no usable connection exists yet, these let the operator
 * (re)authorize in place instead of dead-ending on a link. The OAuth
 * dance carries `returnTo = current wizard path`, so the host bounces the
 * user straight back into onboarding once the token is refreshed. Shared
 * by the app + agent repo-picker steps so the two never drift (#1171).
 */

import { useMutation, useQuery } from "@apollo/client/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { CONNECT_USER_SOURCE_PROVIDER } from "@/graphql/identity/identity.mutations";
import { LIST_MY_CONNECTED_ACCOUNTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftConnectUserSourceProviderPayload,
  AstroliftMyConnectedAccount,
  MutationResult,
} from "@/graphql/identity/identity.types";

const PROVIDERS_SETUP_HREF = "/providers#source";

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
function providerFamily(kind: string): string {
  return kind.split("_")[0] ?? "";
}

interface UseScmConnect {
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

/**
 * Recoverable-auth affordance for a repo picker whose selected
 * connection just 401'd. Offers an in-place (re)authorize against the
 * config that matches the failing connection's provider; falls back to
 * the providers page when the org has no user-connectable config for
 * that host.
 */
export function ScmReauthAction({ connectionKind }: { connectionKind: string }) {
  const { accounts, loading, starting, startConnect } = useScmConnect();
  const family = providerFamily(connectionKind);
  const match =
    accounts.find(
      (a) => providerFamily(a.providerKind) === family && (a.isConnected || a.reauthRequired)
    ) ?? accounts.find((a) => providerFamily(a.providerKind) === family);

  if (match) {
    return (
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="mt-1 h-7"
        disabled={starting}
        onClick={() => startConnect(match.providerConfigId)}
      >
        {match.isConnected ? `Reconnect ${match.providerLabel}` : `Connect ${match.providerLabel}`}
      </Button>
    );
  }
  // Still loading — show nothing extra rather than flashing a fallback
  // link we might immediately replace with the button.
  if (loading) return null;
  return (
    <Link href={PROVIDERS_SETUP_HREF} className="underline">
      Reconnect on the providers page
    </Link>
  );
}

/**
 * Empty-state affordance for a repo picker with no usable connection.
 * If the org has enabled provider configs the viewer hasn't connected,
 * offer to connect them inline; otherwise fall back to the providers
 * page with a note that an org admin has to configure one first.
 */
export function ScmEmptyConnectAction() {
  const { accounts, loading, starting, startConnect } = useScmConnect();
  const connectable = accounts.filter((a) => !a.isConnected);

  if (connectable.length > 0) {
    return (
      <div className="flex flex-wrap gap-2">
        {connectable.map((a) => (
          <Button
            key={a.providerConfigId}
            type="button"
            size="sm"
            variant="outline"
            disabled={starting}
            onClick={() => startConnect(a.providerConfigId)}
          >
            Connect {a.providerLabel}
          </Button>
        ))}
      </div>
    );
  }
  if (loading) return null;
  return (
    <div className="flex flex-col gap-1">
      <Link
        href={PROVIDERS_SETUP_HREF}
        className="text-primary text-sm underline-offset-4 hover:underline"
      >
        Set up a source provider →
      </Link>
      <span className="text-muted-foreground text-xs">
        An org admin must configure a source provider before you can connect a personal identity.
      </span>
    </div>
  );
}
