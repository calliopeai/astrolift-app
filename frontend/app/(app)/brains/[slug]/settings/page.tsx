import { routeTitle } from "@/lib/i18n/route-title";

import { SettingsContent } from "../components/settings-content";

export const generateMetadata = routeTitle("settings", "semanticLayer");

/** Settings: Rebuild and the danger zone (Archive), by `?section=`. */
export default function BrainSettingsPage() {
  return <SettingsContent />;
}
