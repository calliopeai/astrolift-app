import { ConnectedAccountsSection } from "@/components/ConnectedAccountsSection";
import { PageShell } from "@/components/PageShell";
import { GET_MY_PROFILE } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppearanceClient } from "./profile-client";
import { ProfileIdentityClient } from "./identity-client";

export const metadata = { title: "Profile · Settings · Astrolift" };

export default function ProfileSettingsPage() {
  return (
    <PreloadQuery query={GET_MY_PROFILE}>
      <PageShell
        title="Profile"
        description="Your identity, connected accounts, locale, and theme. Identity fields managed by your identity provider sync from there and can't be edited locally."
      >
        <ProfileIdentityClient />
        {/* Linked source accounts (#395). Lived only in the old account
            drawer; the profile is its one home now. */}
        <ConnectedAccountsSection />
        <AppearanceClient />
      </PageShell>
    </PreloadQuery>
  );
}
