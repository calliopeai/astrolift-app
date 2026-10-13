import { routeTitle } from "@/lib/i18n/route-title";

import { AppearanceClient } from "./appearance-client";

export const generateMetadata = routeTitle("appearance", "settings");

export default function AppearancePage() {
  return <AppearanceClient />;
}
