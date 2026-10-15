import { SecurityScreen } from "@/components/screens/settings/account/SecurityScreen";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { PairDeviceCard } from "./pair-device-card";
import { SecuritySettingsClient } from "./security-settings-client";
import { StepUpPinCard } from "./step-up-pin-card";

export const generateMetadata = routeTitle("security", "settings");

export default function SecuritySettingsPage() {
  return (
    <SecurityScreen
      sessions={
        <PreloadQuery query={LIST_ACTIVE_SESSIONS}>
          <SecuritySettingsClient />
        </PreloadQuery>
      }
      stepUpPin={<StepUpPinCard />}
      pairDevice={<PairDeviceCard />}
    />
  );
}
