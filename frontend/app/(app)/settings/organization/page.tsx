import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { OrganizationSettingsClient } from "./organization-settings-client";

export const metadata = { title: "Organization · Settings · Astrolift" };

export default function OrganizationSettingsPage() {
  return (
    <PreloadQuery query={LIST_ORGANIZATIONS}>
      <OrganizationSettingsClient />
    </PreloadQuery>
  );
}
