import { routeTitle } from "@/lib/i18n/route-title";

import { PlatformSettingsClient } from "./platform-settings-client";

export const generateMetadata = routeTitle("platformSettings");

export default function AdministrationPlatformSettingsPage() {
  return <PlatformSettingsClient />;
}
