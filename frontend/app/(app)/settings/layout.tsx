import { SettingsSubnav } from "./settings-subnav";

export const metadata = {
  title: "Settings · Astrolift",
};

/**
 * Shared chrome for the /settings subtree.
 *
 * Uses the same horizontal subnav pattern as /resources and
 * /administration — the previous left-rail sidebar caused a double-nav
 * when combined with the main AppSidebar.
 */
export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col">
      <SettingsSubnav />
      <div className="flex-1">{children}</div>
    </div>
  );
}
