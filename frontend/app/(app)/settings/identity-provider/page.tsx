import { PageShell } from "@/components/PageShell";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata = {
  title: "Identity provider · Settings · Astrolift",
};

export default function IdentityProviderSettingsPage() {
  return (
    <PageShell
      title="Identity provider"
      description="OIDC / SAML / SCIM configuration for the organization. One provider per org."
    >
      <div className="grid gap-4 md:grid-cols-2">
        {[
          {
            title: "OIDC",
            body: "Generic OpenID Connect with discovery URL + client_id + client_secret_ref.",
          },
          {
            title: "SAML 2.0",
            body: "SP-initiated and IdP-initiated flows; metadata URL or XML upload.",
          },
          {
            title: "Auth0 / Okta / Azure AD / Google",
            body: "Pre-configured OIDC profiles. The current dev install uses the auth1 dev-login bypass; staging/prod use Auth0.",
          },
          {
            title: "SCIM 2.0",
            body: "/api/scim/v2 endpoints for User + Group provisioning. SCIM token rotation lives here.",
          },
        ].map((c) => (
          <Card key={c.title}>
            <CardHeader>
              <CardTitle className="text-base">{c.title}</CardTitle>
            </CardHeader>
            <CardContent>
              <CardDescription>{c.body}</CardDescription>
              <p className="text-muted-foreground mt-3 text-xs italic">
                Editing surface arrives once the IdP picker mutation lands.
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </PageShell>
  );
}
