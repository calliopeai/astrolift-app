import { PageShell } from "@/components/PageShell";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SecuritySettingsClient } from "./security-settings-client";

export const metadata = { title: "Security · Settings · Astrolift" };

export default function SecuritySettingsPage() {
  return (
    <PageShell
      title="Security"
      description="Active sessions and bearer credentials issued for your account."
    >
      <PreloadQuery query={LIST_ACTIVE_SESSIONS}>
        <SecuritySettingsClient />
      </PreloadQuery>
    </PageShell>
  );
}
