import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "../permissions-client";
import { ActionSafetyContent } from "./action-safety-client";

export const generateMetadata = routeTitle("actionSafety", "permissions");

export default function ActionSafetyPage() {
  return (
    <PermissionsClient page="safety">
      <ActionSafetyContent />
    </PermissionsClient>
  );
}
