import Link from "next/link";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata = { title: "Security · Settings · Astrolift" };

export default function SecuritySettingsPage() {
  return (
    <PageShell
      title="Security"
      description="Active sessions, multi-factor authentication, and bearer credentials issued for your account."
    >
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>API tokens</CardTitle>
            <CardDescription>
              Long-lived credentials for CLIs, CI runs, and scripts.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline">
              <Link href="/tokens">Manage tokens</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Multi-factor authentication</CardTitle>
            <CardDescription>
              Configured at the IdP layer. Astrolift sees the assertion via OIDC.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Set up MFA in your identity provider (Auth0 / Okta / Azure AD /
              Google Workspace). The platform enforces session-freshness for
              high-risk actions via ABAC policies.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Active sessions</CardTitle>
            <CardDescription>
              Browser and mobile sessions currently signed in.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Lands once the session-list query is wired. Until then, sign out
              from the user menu in the sidebar.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Admin elevation</CardTitle>
            <CardDescription>
              Time-boxed re-auth for cross-org operator actions.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Astrolift operators (not customers) can request a 1h elevation
              window from a separate admin endpoint. Every action under the
              elevated session is audited individually.
            </p>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
