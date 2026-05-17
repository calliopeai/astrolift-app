import { SettingsShell } from "./settings-shell";

export const metadata = {
  title: "Settings · Astrolift",
};

/**
 * Shared chrome for the /settings subtree (#414).
 *
 * Renders a persistent left rail listing every settings sub-route
 * grouped by section (Organization / Personal / Tokens), a
 * `Settings › Section › Page` breadcrumb above each sub-route's
 * content, and a top-sheet fallback on mobile.
 *
 * Sub-routes own their own `PageShell` for title + description; this
 * layout adds only the cross-page chrome.
 */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <SettingsShell>{children}</SettingsShell>;
}
