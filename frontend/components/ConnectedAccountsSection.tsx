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

import { useTranslations } from "next-intl";
import * as React from "react";
import { GithubIcon, GitlabIcon, GitBranchIcon } from "lucide-react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { useConnectedAccounts } from "@/components/use-connected-accounts";
import type { AstroliftMyConnectedAccount } from "@/graphql/identity/identity.types";

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
  const t = useTranslations("securitySettings.connected");
  if (!account.isConnected) {
    return (
      <Badge variant="outline" className="text-2xs">
        {t("notConnected")}
      </Badge>
    );
  }
  if (account.reauthRequired) {
    return (
      <Badge
        variant="outline"
        className="border-warning-border bg-warning/10 text-2xs text-warning-fg"
      >
        {t("reauthorize")}
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="border-success-border bg-success/10 text-2xs text-success-fg"
    >
      {t("connected")}
    </Badge>
  );
}

export type ConnectedAccountsSectionProps = ReturnType<typeof useConnectedAccounts>;

/** Pure (Storybook first): accounts and actions come from useConnectedAccounts. */
export function ConnectedAccountsSection({
  accounts,
  loading,
  connecting,
  disconnecting,
  onConnect,
  onDisconnect,
}: ConnectedAccountsSectionProps) {
  const [disconnectTarget, setDisconnectTarget] =
    React.useState<AstroliftMyConnectedAccount | null>(null);
  const [confirmInput, setConfirmInput] = React.useState("");
  const t = useTranslations("securitySettings.connected");

  // Hide entirely when the org hasn't enabled any user-flow source
  // providers. Don't surface a placeholder — operators shouldn't see
  // a section for a feature their org doesn't have.
  if (!loading && accounts.length === 0) return null;
  if (loading && accounts.length === 0) return null;

  async function handleDisconnect() {
    if (disconnectTarget === null) return;
    await onDisconnect(disconnectTarget, confirmInput);
    setConfirmInput("");
  }

  return (
    <>
      <div className="border-t pt-4">
        <p className="text-muted-foreground mb-3 text-xs font-medium tracking-wide uppercase">
          {t("title")}
        </p>
        <div className="flex flex-col gap-3">
          {accounts.map((account) => (
            <div key={account.providerConfigId} className="flex items-start justify-between gap-3">
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
                    disabled={connecting}
                    onClick={() => onConnect(account)}
                  >
                    {t("connect")}
                  </Button>
                ) : account.reauthRequired ? (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={connecting}
                    onClick={() => onConnect(account)}
                  >
                    {t("reauthorize")}
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disconnecting}
                    onClick={() => {
                      setDisconnectTarget(account);
                      setConfirmInput("");
                    }}
                  >
                    {t("disconnect")}
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="text-muted-foreground text-2xs mt-3">{t("refetchHint")}</p>
      </div>

      <ConfirmDialog
        open={disconnectTarget !== null}
        onOpenChange={(next) => {
          if (!next) {
            setDisconnectTarget(null);
            setConfirmInput("");
          }
        }}
        title={t("disconnectTitle", {
          provider: disconnectTarget?.providerLabel ?? t("thisAccount"),
        })}
        description={
          <div className="flex flex-col gap-3">
            <p>
              {t.rich("disconnectBody", {
                account: disconnectTarget?.linkedAccountLogin ?? t("thisAccount"),
                provider: disconnectTarget?.providerLabel ?? "",
                mono: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
            </p>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="connected-accounts-disconnect-confirm" className="text-xs">
                {t.rich("typeToConfirm", {
                  account: disconnectTarget?.linkedAccountLogin ?? t("theLinkedAccount"),
                  mono: (chunks) => <span className="font-mono">{chunks}</span>,
                })}
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
        confirmLabel={t("disconnect")}
        destructive
        onConfirm={handleDisconnect}
      />
    </>
  );
}
