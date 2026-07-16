import { LIST_POLICIES } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { PoliciesClient } from "./policies-client";

export const metadata = { title: "Policies · Administration · Astrolift" };

export default function PoliciesAdministrationPage() {
  return (
    <PreloadQuery query={LIST_POLICIES}>
      <PoliciesClient />
    </PreloadQuery>
  );
}
