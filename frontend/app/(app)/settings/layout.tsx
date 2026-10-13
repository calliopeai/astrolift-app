import { SettingsShell } from "@/components/screens/settings/shell/SettingsShell";
import { routeTitle } from "@/lib/i18n/route-title";

import { SettingsSubnav } from "./settings-subnav";

export const generateMetadata = routeTitle("settings");

/** Shared chrome for the /settings subtree; the view is SettingsShell. */
export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  return <SettingsShell subnav={<SettingsSubnav />}>{children}</SettingsShell>;
}
