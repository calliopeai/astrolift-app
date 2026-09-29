"use client";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { useTokenDetail } from "./use-token-detail";

export type TokenDetailScreenProps = ReturnType<typeof useTokenDetail>;

// Module-level so the impure Date.now() isn't a render-phase call
// (react-hooks/purity). Display-only; re-evaluated on each render/poll.
function isExpired(expiresAt: string | null | undefined): boolean {
  return expiresAt ? new Date(expiresAt).getTime() < Date.now() : false;
}

/**
 * API token detail (#1106). Metadata only — the plaintext secret is shown
 * exactly once at creation and never stored, so only the non-secret last-4
 * suffix is displayed here.
 */
export function TokenDetailScreen({ id, token: tk, loading }: TokenDetailScreenProps) {
  const expired = isExpired(tk?.expiresAt);
  const state = tk ? (tk.isRevoked ? "revoked" : expired ? "expired" : "active") : undefined;
  const stateTone: Dot | undefined =
    state === "active"
      ? "ok"
      : state === "revoked"
        ? "error"
        : state === "expired"
          ? "muted"
          : undefined;

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!tk}
      breadcrumb={{ label: "API keys", href: "/tokens" }}
      heading={tk ? tk.name : `Token ${id.slice(0, 8)}`}
      status={state}
      statusTone={stateTone}
      createdAt={tk?.createdAt}
      notFoundLabel="API token"
      overview={
        tk
          ? [
              { term: "Name", description: <span className="font-medium">{tk.name}</span> },
              {
                term: "Token",
                description: <span className="font-mono text-xs">••••{tk.tokenLast4}</span>,
              },
              {
                term: "Owner",
                description: <span className="font-mono text-xs">{tk.user.username}</span>,
              },
              {
                term: "Team",
                description: tk.teamSlug ? (
                  <span className="font-mono text-xs">{tk.teamSlug}</span>
                ) : (
                  <span className="text-muted-foreground">— (org-wide)</span>
                ),
              },
              { term: "Expires", description: <DetailTimestamp iso={tk.expiresAt} /> },
              { term: "Last used", description: <DetailTimestamp iso={tk.lastUsedAt} /> },
              {
                term: "Last used IP",
                description: tk.lastUsedIp ? (
                  <span className="font-mono text-xs">{tk.lastUsedIp}</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: "Last used agent",
                description: tk.lastUsedAgent ? (
                  <span className="font-mono text-xs break-all">{tk.lastUsedAgent}</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              { term: "Created", description: <DetailTimestamp iso={tk.createdAt} /> },
            ]
          : []
      }
    >
      {tk ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Scopes</CardTitle>
          </CardHeader>
          <CardContent>
            {tk.scopes.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No scopes — this token has no granted permissions.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {tk.scopes.map((scope) => (
                  <Badge key={scope} variant="outline" className="font-mono text-xs">
                    {scope}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
