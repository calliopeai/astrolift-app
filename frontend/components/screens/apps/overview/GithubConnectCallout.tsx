"use client";

import { GithubIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

import { Notice } from "./OverviewNotices";
import type { useSourceConnections } from "./use-source-connections";

export type GithubConnectCalloutViewProps = ReturnType<typeof useSourceConnections> & {
  /** App-level source kind from `astroliftApp.sourceKind`. */
  sourceKind: string;
};

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
export function GithubConnectCalloutView({
  sourceKind,
  loading,
  connections,
}: GithubConnectCalloutViewProps) {
  if (sourceKind !== "github") return null;
  if (loading) return null;

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
    <Notice
      tone="info"
      icon={GithubIcon}
      title="Connect your GitHub to manage this app"
      description="Astrolift uses your personal GitHub identity to browse the repo, sync the manifest, and install webhooks. Org-level setup is already in place; this is just the per-user link."
      actions={
        <Button asChild size="sm" variant="outline">
          <a href={href}>Connect GitHub</a>
        </Button>
      }
    />
  );
}
