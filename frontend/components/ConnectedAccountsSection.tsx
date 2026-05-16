"use client";

/**
 * Per-user "connected accounts" surface for the account drawer.
 *
 * Renders one row per source provider the org admin has enabled, with
 * the viewer's personal connection state (connected / not connected /
 * re-auth required) and a contextual action button. Hides entirely
 * when the org has no user-flow providers enabled — operators don't
 * see a placeholder for a feature their org doesn't have.
 *
 * The connect action redirects ``window.location`` to the OAuth
 * authorize URL the backend returns. The disconnect action requires
 * the operator to type the linked account login as a typed-confirm
 * so a stray click can't strand their deploys.
 *
 * Issue: #395.
 */

import * as React from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { GithubIcon, GitlabIcon, GitBranchIcon } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

/** Resolve a provider-kind into the icon we render alongside its label. */
function providerIcon(kind: string): React.ReactNode {
  if (kind.startsWith("github")) {
    return <GithubIcon className="size-4" aria-hidden />;
  }
  if (kind.startsWith("gitlab")) {
    return <GitlabIcon className="size-4" aria-hidden />;
  }
  return <GitBranchIcon className="size-4" aria-hidden />;
}

interface StatusChipProps {
  account: AstroliftMyConnectedAccount;
}

function StatusChip({ account }: StatusChipProps) {
  if (!account.isConnected) {
    return (
      <Badge variant="outline" className="text-[10px]">
        Not connected
      </Badge>
    );
  }
  if (account.reauthRequired) {
    return (
      <Badge
        variant="outline"
        className="border-amber-500/40 bg-amber-500/10 text-[10px] text-amber-700 dark:text-amber-300"
      >
        Re-authorize
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="border-emerald-500/40 bg-emerald-500/10 text-[10px] text-emerald-700 dark:text-emerald-300"
    >
      Connected
    </Badge>
  );
}

export function ConnectedAccountsSection() {
  const { data, loading, refetch } = useQuery<ListResp>(LIST_MY_CONNECTED_ACCOUNTS, {
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

  const [disconnectTarget, setDisconnectTarget] =
    React.useState<AstroliftMyConnectedAccount | null>(null);
  const [confirmInput, setConfirmInput] = React.useState("");

  const accounts = data?.astroliftMyConnectedAccounts ?? [];

  // Hide entirely when the org hasn't enabled any user-flow source
  // providers. Don't surface a placeholder — operators shouldn't see
  // a section for a feature their org doesn't have.
  if (!loading && accounts.length === 0) return null;
  if (loading && accounts.length === 0) return null;

  async function handleConnect(account: AstroliftMyConnectedAccount) {
    try {
      const { data } = await connect({
        variables: { input: { providerConfigId: account.providerConfigId } },
      });
      const payload = data?.astroliftConnectUserSourceProvider;
      if (!payload?.ok || !payload.data) {
        toast.error(
          payload?.errors?.[0]?.message ?? "Could not start the OAuth flow"
        );
        return;
      }
      // Hand off to the OAuth start endpoint. The host bounces back
      // to /settings/source-providers (or wherever the start view
      // sends ``return_to``); a router refetch will pick up the new
      // connection state on return.
      window.location.href = payload.data.authorizationUrl;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connect failed");
    }
  }

  async function handleDisconnect() {
    if (disconnectTarget === null) return;
    const { data } = await disconnect({
      variables: {
        input: {
          providerConfigId: disconnectTarget.providerConfigId,
          confirmAccountLogin: confirmInput,
        },
      },
    });
    const payload = data?.astroliftDisconnectUserSourceProvider;
    if (!payload?.ok) {
      throw new Error(
        payload?.errors?.[0]?.message ?? "Could not disconnect this account"
      );
    }
    toast.success(`Disconnected ${disconnectTarget.linkedAccountLogin ?? "account"}`);
    setConfirmInput("");
  }

  return (
    <>
      <div className="border-t pt-4">
        <p className="text-muted-foreground mb-3 text-xs font-medium uppercase tracking-wide">
          Connected accounts
        </p>
        <div className="flex flex-col gap-3">
          {accounts.map((account) => (
            <div
              key={account.providerConfigId}
              className="flex items-start justify-between gap-3"
            >
              <div className="flex min-w-0 items-start gap-2">
                <span className="text-muted-foreground mt-0.5">
                  {providerIcon(account.providerKind)}
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium">{account.providerLabel}</span>
                    <StatusChip account={account} />
                  </div>
                  {account.isConnected && account.linkedAccountLogin && (
                    <p className="text-muted-foreground font-mono text-xs">
                      {account.linkedAccountLogin}
                    </p>
                  )}
                </div>
              </div>
              <div className="shrink-0">
                {!account.isConnected ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={connectState.loading}
                    onClick={() => handleConnect(account)}
                  >
                    Connect
                  </Button>
                ) : account.reauthRequired ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={connectState.loading}
                    onClick={() => handleConnect(account)}
                  >
                    Re-authorize
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disconnectState.loading}
                    onClick={() => {
                      setDisconnectTarget(account);
                      setConfirmInput("");
                    }}
                  >
                    Disconnect
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="text-muted-foreground mt-3 text-[11px]">
          Refetch on focus by reloading the page — connection state updates after the OAuth
          dance completes on the host.
        </p>
      </div>

      <ConfirmDialog
        open={disconnectTarget !== null}
        onOpenChange={(next) => {
          if (!next) {
            setDisconnectTarget(null);
            setConfirmInput("");
          }
        }}
        title={`Disconnect ${disconnectTarget?.providerLabel ?? "this account"}?`}
        description={
          <div className="flex flex-col gap-3">
            <p>
              Astrolift will lose access to{" "}
              <span className="font-mono">
                {disconnectTarget?.linkedAccountLogin ?? "this account"}
              </span>{" "}
              on {disconnectTarget?.providerLabel}. Any deploys that flow through your
              identity will fail until you reconnect.
            </p>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="connected-accounts-disconnect-confirm" className="text-xs">
                Type{" "}
                <span className="font-mono">
                  {disconnectTarget?.linkedAccountLogin ?? "the linked account"}
                </span>{" "}
                to confirm.
              </Label>
              <Input
                id="connected-accounts-disconnect-confirm"
                autoComplete="off"
                value={confirmInput}
                onChange={(event) => setConfirmInput(event.target.value)}
              />
            </div>
          </div>
        }
        confirmLabel="Disconnect"
        destructive
        onConfirm={handleDisconnect}
      />
    </>
  );
}
