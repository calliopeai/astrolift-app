import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Identity providers · Documentation · Astrolift",
};

const kinds = [
  {
    id: "oidc",
    title: "Generic OIDC",
    badge: "OIDC",
    intro:
      "The most flexible option. Works with any provider that publishes an OpenID Connect discovery document.",
    steps: [
      "At the IdP, register a new OIDC client. Paste the redirect URI shown in the Astrolift dialog (it ends with /app/auth1/callback).",
      "Copy the client_id, client_secret, and the discovery URL (the .well-known/openid-configuration endpoint).",
      "In Astrolift, open Settings · Identity providers · New provider, pick OIDC, paste the values, and Set active.",
    ],
  },
  {
    id: "cognito",
    title: "Amazon Cognito",
    badge: "OIDC",
    intro:
      "Cognito user pools speak OIDC. Use this when your install lives in AWS and you already manage users in a pool.",
    steps: [
      "In the AWS console, open the user pool · App integration · Create an app client. Pick a confidential client and add the Astrolift redirect URI.",
      "Note the User Pool ID, App client ID, and App client secret. The discovery URL is https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/openid-configuration.",
      "In Astrolift, New provider · Cognito, paste the three values, Set active.",
    ],
  },
  {
    id: "auth0",
    title: "Auth0",
    badge: "OIDC",
    intro:
      "Auth0 is the default for Calliope-hosted installs. Use Auth0 if you want hosted MFA, passwordless, or social logins without running them yourself.",
    steps: [
      "In Auth0, Applications · Create Application · Regular Web App. Set Allowed Callback URLs to the redirect URI from the Astrolift dialog.",
      "Copy the Domain, Client ID, and Client Secret. The discovery URL is https://<your-tenant>.auth0.com/.well-known/openid-configuration.",
      "In Astrolift, New provider · Auth0, paste the values, Set active.",
    ],
  },
  {
    id: "okta",
    title: "Okta",
    badge: "OIDC",
    intro:
      "Okta is the most common enterprise choice. Use Okta's OIDC, not the SAML app — OIDC integrates cleanly with Astrolift's session model.",
    steps: [
      "In Okta admin, Applications · Create App Integration · OIDC · Web Application. Add the Astrolift redirect URI under Sign-in redirect URIs.",
      "Copy the Client ID, Client Secret, and the Issuer URL (visible under General · OpenID Connect ID Token).",
      "In Astrolift, New provider · Okta, paste the values, Set active.",
    ],
  },
  {
    id: "azure-ad",
    title: "Azure AD / Entra ID",
    badge: "OIDC",
    intro:
      "Entra (formerly Azure AD) is the right pick if your org is on Microsoft 365.",
    steps: [
      "In the Azure portal, App registrations · New registration. Set the redirect URI to the value from Astrolift and platform to Web.",
      "Under Certificates & secrets, create a client secret and copy its value. Note the Application (client) ID and Directory (tenant) ID.",
      "The discovery URL is https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration. Paste it plus the client ID and secret into Astrolift, Set active.",
    ],
  },
  {
    id: "google",
    title: "Google Workspace",
    badge: "OIDC",
    intro:
      "Google Workspace OIDC works when every Astrolift operator has a workspace account.",
    steps: [
      "In Google Cloud console, APIs & Services · Credentials · Create OAuth client ID · Web application. Add the Astrolift redirect URI.",
      "Copy the Client ID and Client Secret. The discovery URL is https://accounts.google.com/.well-known/openid-configuration.",
      "In Astrolift, New provider · Google, paste the values, Set active. Combine with the domain allowlist below to limit sign-in to your workspace.",
    ],
  },
  {
    id: "github",
    title: "GitHub OAuth",
    badge: "OAuth",
    intro:
      "GitHub for sign-in only (independent of the SCM connection from Source providers). Useful for small teams that already have a GitHub org.",
    steps: [
      "In GitHub, Settings · Developer settings · OAuth Apps · New OAuth App. Set the callback URL to the value from Astrolift.",
      "Copy the Client ID and generate a Client Secret.",
      "In Astrolift, New provider · GitHub OAuth, paste the values, Set active.",
    ],
  },
  {
    id: "saml",
    title: "SAML 2.0",
    badge: "SAML",
    intro:
      "For IdPs that only speak SAML (older enterprise installs, some federated setups).",
    steps: [
      "At the IdP, register a new SAML application. Set the ACS URL and Entity ID to the values shown in the Astrolift dialog.",
      "Download the IdP metadata XML or copy its URL.",
      "In Astrolift, New provider · SAML, paste the metadata URL (or upload the XML), Set active.",
    ],
  },
  {
    id: "local",
    title: "Local accounts",
    badge: "Local",
    intro:
      "First-run / fallback only. Local accounts let an operator log in with a username and password stored in the Astrolift database. Use them while you are wiring an SSO provider, then turn them off.",
    steps: [
      "Local accounts are enabled by default on a fresh install. The bootstrap admin lives in the database; see the install runbook for how it is seeded.",
      "Once SSO is configured and verified, set the SSO provider active and either delete the local entry or block password sign-in via policy.",
    ],
  },
];

export default function IdentityProvidersDocPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Identity providers</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Route sign-in through your corporate IdP: OIDC, SAML, Cognito,
          Auth0, Okta, Azure AD, Google Workspace, GitHub, or local accounts.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          You want users to sign in with their existing corporate identity
          instead of a separate Astrolift password, and you want central
          control over who has access (deprovision in the IdP, lose access
          here). Exactly one provider is active at a time; the rest are
          standby.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Prerequisites</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Permission.</strong> An
            operator role with <code>org.update</code> (Owner and Admin have
            it).
          </li>
          <li>
            <strong className="text-foreground">Admin access to the IdP.</strong>{" "}
            You will register a new OIDC or SAML client there.
          </li>
          <li>
            <strong className="text-foreground">
              Astrolift&apos;s redirect URI.
            </strong>{" "}
            Visible in the <strong>New provider</strong> dialog. Format is{" "}
            <code>https://&lt;your-install&gt;/app/auth1/callback</code>.
            Every IdP needs this exact value in its allowed callback list.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Configure by provider kind</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Pick the matching subsection. Every kind ends with the same three
          clicks in Astrolift:{" "}
          <Link
            href="/settings/identity-provider"
            className="text-foreground underline-offset-2 hover:underline"
          >
            Settings · Identity providers
          </Link>{" "}
          → <strong>New provider</strong> → pick the kind → paste the
          credentials → <strong>Set active</strong>.
        </p>
      </section>

      {kinds.map((k) => (
        <section key={k.id} id={k.id} className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-medium">{k.title}</h3>
            <Badge variant="outline">{k.badge}</Badge>
          </div>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {k.intro}
          </p>
          <ol className="text-muted-foreground flex flex-col gap-2 pl-5 text-sm">
            {k.steps.map((s, i) => (
              <li key={i} className="list-decimal leading-relaxed">
                {s}
              </li>
            ))}
          </ol>
        </section>
      ))}

      <Separator />

      <section id="domain-allowlist" className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Domain allowlist (auto-signup)</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          By default, an SSO sign-in succeeds only if Astrolift already has
          an account for that user. To let new users self-onboard, mark their
          email domains as trusted.
        </p>
        <ol className="text-muted-foreground flex flex-col gap-2 pl-5 text-sm">
          <li className="list-decimal leading-relaxed">
            Open{" "}
            <Link
              href="/administration/organization"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Settings · Organization
            </Link>
            .
          </li>
          <li className="list-decimal leading-relaxed">
            Scroll to the <strong>Trusted email domains</strong> card and add{" "}
            <code>corp.com</code> (no <code>@</code>).
          </li>
          <li className="list-decimal leading-relaxed">
            Save. Anyone who signs in via SSO with an{" "}
            <code>@corp.com</code> email is auto-joined to the organization
            with the default member role.
          </li>
        </ol>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              &ldquo;No IdP configured.&rdquo;
            </strong>{" "}
            The install&apos;s <code>bootstrap_idp</code> environment variables
            were not set. Configure a provider from{" "}
            <Link
              href="/settings/identity-provider"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Settings · Identity providers
            </Link>{" "}
            and set it active.
          </li>
          <li>
            <strong className="text-foreground">
              Login redirects to the wrong host (or returns redirect_uri_mismatch).
            </strong>{" "}
            <code>APP_BASE_URL</code> in the backend and the redirect URI
            registered at the IdP must match exactly — protocol, host, path,
            and trailing slash.
          </li>
          <li>
            <strong className="text-foreground">Auto-signup not happening.</strong>{" "}
            The user&apos;s email domain isn&apos;t in the trusted list. See{" "}
            <Link
              href="#domain-allowlist"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Domain allowlist
            </Link>{" "}
            above.
          </li>
          <li>
            <strong className="text-foreground">
              SAML signature verification fails.
            </strong>{" "}
            The IdP rotated its signing certificate. Re-paste the metadata URL
            (or upload the fresh XML) — Astrolift refreshes the trust store on
            save.
          </li>
        </ul>
      </section>
    </article>
  );
}
