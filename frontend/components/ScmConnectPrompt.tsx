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

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { providerFamily, type UseScmConnect } from "@/components/use-scm-connect";

const PROVIDERS_SETUP_HREF = "/providers#source";

/**
 * Recoverable-auth affordance for a repo picker whose selected
 * connection just 401'd. Offers an in-place (re)authorize against the
 * config that matches the failing connection's provider; falls back to
 * the providers page when the org has no user-connectable config for
 * that host.
 */
export function ScmReauthAction({
  connectionKind,
  scm,
}: {
  connectionKind: string;
  scm: UseScmConnect;
}) {
  const { accounts, loading, starting, startConnect } = scm;
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
export function ScmEmptyConnectAction({ scm }: { scm: UseScmConnect }) {
  const { accounts, loading, starting, startConnect } = scm;
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
