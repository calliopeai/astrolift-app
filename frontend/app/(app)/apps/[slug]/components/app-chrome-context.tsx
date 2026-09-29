"use client";

import * as React from "react";

/**
 * The URL prefix a RegisteredApp's platform pages navigate under. Normally
 * `/apps`, but an agent IS a RegisteredApp — when its platform sub-pages
 * (config, settings, secrets, deployments…) render inside the agent shell we
 * keep the operator in agent context and navigate under `/agents` instead.
 */
export type AppChromeBasePath = "/apps" | "/agents";

export interface AppChrome {
  /** Prefix for slug-scoped navigation to this app's own sub-pages. */
  basePath: AppChromeBasePath;
  /**
   * True when the app client is rendered inside the full agent shell
   * (`AgentDetailShell`). Drives content-only chrome: the client's own
   * `PageShell` header and its `AppTabs` bar are suppressed so the agent
   * shell's header + BROCS pillar tabs + platform-links row are the only
   * page chrome (no duplicated headers or tab bars).
   */
  agentShell: boolean;
  /**
   * True inside the app frame (`/apps/[slug]/layout.tsx`): the frame draws
   * the header and the one row of tabs, so each client's `PageShell` drops
   * its title and its `AppTabs` render nothing. Actions and description stay.
   */
  framed: boolean;
}

/**
 * Default chrome for every `/apps/*` route: no provider is mounted there, so
 * `useAppChrome()` returns this and all existing app usage is byte-identical
 * (base `/apps`, full per-client chrome).
 */
const DEFAULT_CHROME: AppChrome = { basePath: "/apps", agentShell: false, framed: false };

const AppChromeContext = React.createContext<AppChrome>(DEFAULT_CHROME);

interface AppChromeProviderProps {
  basePath?: AppChromeBasePath;
  agentShell?: boolean;
  framed?: boolean;
  children: React.ReactNode;
}

/**
 * Provide app-chrome context to a subtree. Mounted by the agent shell
 * around a shared app client (`agentShell`), and by the app frame around
 * every `/apps/[slug]/*` route (`framed`). Anything outside both falls
 * through to {@link DEFAULT_CHROME}.
 */
export function AppChromeProvider({
  basePath = "/apps",
  agentShell = false,
  framed = false,
  children,
}: AppChromeProviderProps) {
  const value = React.useMemo<AppChrome>(
    () => ({ basePath, agentShell, framed }),
    [basePath, agentShell, framed]
  );
  return <AppChromeContext.Provider value={value}>{children}</AppChromeContext.Provider>;
}

/**
 * Read the ambient app chrome. With no provider mounted (every `/apps` route)
 * this returns {@link DEFAULT_CHROME} — `{ basePath: "/apps", agentShell:
 * false }` — so existing app links resolve exactly as before.
 */
export function useAppChrome(): AppChrome {
  return React.useContext(AppChromeContext);
}

/**
 * Build a slug-scoped path under the active base. Matches the app surface's
 * raw interpolation (no slug encoding) so `/apps` links are byte-identical:
 *
 *   appPath(chrome, "acme")               → `${base}/acme`
 *   appPath(chrome, "acme", "settings")   → `${base}/acme/settings`
 *   appPath(chrome, "acme", "workloads", w) → `${base}/acme/workloads/${w}`
 *
 * where `base` is `chrome.basePath` (`/apps` by default, `/agents` inside the
 * agent shell). Callers append query strings themselves.
 */
export function appPath(chrome: AppChrome, slug: string, ...segments: string[]): string {
  const base = `${chrome.basePath}/${slug}`;
  return segments.length === 0 ? base : `${base}/${segments.join("/")}`;
}
