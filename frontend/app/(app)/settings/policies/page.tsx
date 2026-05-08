import { LIST_POLICIES } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { PoliciesClient } from "./policies-client";

export const metadata = { title: "Policies · Settings · Astrolift" };

export default function PoliciesSettingsPage() {
  return (
    <PreloadQuery query={LIST_POLICIES}>
      <PoliciesClient />
    </PreloadQuery>
  );
}
