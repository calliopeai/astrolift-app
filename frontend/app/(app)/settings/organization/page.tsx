import { OrganizationSettingsClient } from "./organization-settings-client";

export const metadata = { title: "Organization · Settings · Astrolift" };

// LIST_ORGANIZATIONS is primed at the (app) layout level — see #269 —
// so this page reads from cache. No PreloadQuery needed here.
export default function OrganizationSettingsPage() {
  return <OrganizationSettingsClient />;
}
