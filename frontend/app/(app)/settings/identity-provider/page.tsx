import { LIST_IDENTITY_PROVIDERS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { IdentityProviderClient } from "./identity-provider-client";

export const metadata = {
  title: "Identity provider · Settings · Astrolift",
};

export default function IdentityProviderSettingsPage() {
  return (
    <PreloadQuery query={LIST_IDENTITY_PROVIDERS}>
      <IdentityProviderClient />
    </PreloadQuery>
  );
}
