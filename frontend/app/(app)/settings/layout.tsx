import { SettingsShell } from "@/components/screens/settings/shell/SettingsShell";

import { SettingsSubnav } from "./settings-subnav";

export const metadata = {
  title: "Settings · Astrolift",
};

/** Shared chrome for the /settings subtree; the view is SettingsShell. */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <SettingsShell subnav={<SettingsSubnav />}>{children}</SettingsShell>;
}
