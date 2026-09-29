import { SecurityScreen } from "@/components/screens/settings/account/SecurityScreen";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { PairDeviceCard } from "./pair-device-card";
import { SecuritySettingsClient } from "./security-settings-client";

export const metadata = { title: "Security · Settings · Astrolift" };

export default function SecuritySettingsPage() {
  return (
    <SecurityScreen
      sessions={
        <PreloadQuery query={LIST_ACTIVE_SESSIONS}>
          <SecuritySettingsClient />
        </PreloadQuery>
      }
      pairDevice={<PairDeviceCard />}
    />
  );
}
