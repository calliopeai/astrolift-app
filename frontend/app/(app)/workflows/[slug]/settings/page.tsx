import { routeTitle } from "@/lib/i18n/route-title";

import { SettingsContent } from "../components/settings-content";

export const generateMetadata = routeTitle("settings", "workflow");

/** The Settings tab (spec 44 §5.2, §5.3): General and one Danger zone, by `?section=`. */
export default function WorkflowSettingsPage() {
  return <SettingsContent />;
}
