import { PageShell } from "@/components/PageShell";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { PairDeviceCard } from "./pair-device-card";
import { SecuritySettingsClient } from "./security-settings-client";

export const metadata = { title: "Security · Settings · Astrolift" };

export default function SecuritySettingsPage() {
  return (
    <PageShell
      title="Security"
      description="Active sessions and bearer credentials issued for your account."
    >
      <div className="grid gap-4">
        <PreloadQuery query={LIST_ACTIVE_SESSIONS}>
          <SecuritySettingsClient />
        </PreloadQuery>
        <PairDeviceCard />
      </div>
    </PageShell>
  );
}
