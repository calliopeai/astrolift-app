import { routeTitle } from "@/lib/i18n/route-title";

import { PermissionsClient } from "../permissions-client";
import { AccessPresetsContent } from "./access-presets-client";

export const generateMetadata = routeTitle("easySetup", "permissions");

export default function AccessPresetsPage() {
  return (
    <PermissionsClient page="presets">
      <AccessPresetsContent />
    </PermissionsClient>
  );
}
