import { PageShell } from "@/components/PageShell";

import { HomeSettingsClient } from "./home-settings-client";

export const metadata = { title: "Home · Settings · Astrolift" };

export default function HomeSettingsPage() {
  return (
    <PageShell title="Home" description="How Home is arranged for you.">
      <HomeSettingsClient />
    </PageShell>
  );
}
