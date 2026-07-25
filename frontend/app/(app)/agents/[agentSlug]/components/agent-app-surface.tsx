"use client";

import * as React from "react";

import { AppChromeProvider } from "@/app/(app)/apps/[slug]/components/app-chrome-context";

import { AgentDetailShell } from "./agent-detail-shell";

/**
 * Renders one of a RegisteredApp's platform sub-pages (config, settings,
 * secrets, deployments…) inside the full agent shell.
 *
 * An agent IS a RegisteredApp, so those pages are the shared `/apps/[slug]/*`
 * clients — imported here unmodified. The chrome comes entirely from
 * `AgentDetailShell` (agent header + BROCS pillar tabs + platform-links row);
 * the app client renders content-only because it's wrapped in the
 * `AppChromeProvider` with `agentShell` set, which makes its own `PageShell`
 * drop its header and its `AppTabs` render nothing. `basePath="/agents"` keeps
 * every slug-scoped link the client emits inside agent context.
 *
 * The provider sits *inside* the shell's render-prop children, so the shell's
 * own header (the one PageShell above) is unaffected — only the nested app
 * client goes content-only.
 */
export function AgentAppSurface({
  agentSlug,
  children,
}: {
  agentSlug: string;
  children: React.ReactNode;
}) {
  return (
    <AgentDetailShell agentSlug={agentSlug}>
      {() => (
        <AppChromeProvider basePath="/agents" agentShell>
          {children}
        </AppChromeProvider>
      )}
    </AgentDetailShell>
  );
}
