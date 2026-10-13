import { routeTitle } from "@/lib/i18n/route-title";

import { OrganizationSettingsClient } from "./organization-settings-client";

export const generateMetadata = routeTitle("organization", "administration");

// LIST_ORGANIZATIONS is primed at the (app) layout level — see #269 —
// so this page reads from cache. No PreloadQuery needed here.
export default function OrganizationAdministrationPage() {
  return <OrganizationSettingsClient />;
}
