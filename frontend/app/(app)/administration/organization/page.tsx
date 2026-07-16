import { OrganizationSettingsClient } from "./organization-settings-client";

export const metadata = { title: "Organization · Administration · Astrolift" };

// LIST_ORGANIZATIONS is primed at the (app) layout level — see #269 —
// so this page reads from cache. No PreloadQuery needed here.
export default function OrganizationAdministrationPage() {
  return <OrganizationSettingsClient />;
}
