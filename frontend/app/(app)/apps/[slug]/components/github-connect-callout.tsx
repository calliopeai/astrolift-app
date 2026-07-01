"use client";

import { useQuery } from "@apollo/client/react";
import { GithubIcon } from "lucide-react";

import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface Props {
  /** App-level source kind from `astroliftApp.sourceKind`. */
  sourceKind: string;
}

/**
 * Prompts the viewer to connect their personal GitHub when:
 *   - the app's source is GitHub
 *   - the viewer has no `github_oauth_user` (or `github_pat`) connection
 *
 * Connecting unlocks repo browsing, manifest sync, and webhook install
 * actions that flow through the viewer's identity. The org-level OAuth
 * App config is separate (`github_oauth_app`) — that's the operator's
 * setup and isn't what gates these per-user actions.
 */
export function GithubConnectCallout({ sourceKind }: Props) {
  const { data, loading } = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
  });

  if (sourceKind !== "github") return null;
  if (loading) return null;

  const connections = data?.astroliftSourceConnections ?? [];
  const hasUserConn = connections.some(
    (c) => c.isActive && c.isPersonal && (c.kind === "github_oauth_user" || c.kind === "github_pat")
  );
  if (hasUserConn) return null;

  // Find the OAuth-app config we should hand to the start endpoint. Any
  // active GitHub OAuth-app config works — the backend picks the right
  // one when only one exists.
  const oauthAppCfg = connections.find((c) => c.isActive && c.kind === "github_oauth_app");
  const href = oauthAppCfg
    ? `/app/auth1/scm/github/start?config_id=${oauthAppCfg.id}`
    : `/settings/source-providers`;

  return (
    <section className="flex flex-wrap items-start gap-3 rounded-lg border border-warning-border bg-warning/5 p-4">
      <GithubIcon className="mt-0.5 size-4 shrink-0 text-warning-fg" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">Connect your GitHub to manage this app</p>
        <p className="text-muted-foreground mt-1 max-w-2xl text-xs">
          Astrolift uses your personal GitHub identity to browse the repo, sync the manifest, and
          install webhooks. Org-level setup is already in place — this is just the per-user link.
        </p>
      </div>
      <a
        href={href}
        className="bg-foreground text-background hover:bg-foreground/90 shrink-0 rounded-md px-3 py-1.5 text-xs font-medium"
      >
        Connect GitHub
      </a>
    </section>
  );
}
