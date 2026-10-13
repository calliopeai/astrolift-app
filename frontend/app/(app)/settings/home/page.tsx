import { PageShell } from "@/components/PageShell";
import { routeTitle } from "@/lib/i18n/route-title";

import { HomeSettingsClient } from "./home-settings-client";

export const generateMetadata = routeTitle("home", "settings");

export default function HomeSettingsPage() {
  return (
    <PageShell title="Home" description="How Home is arranged for you.">
      <HomeSettingsClient />
    </PageShell>
  );
}
