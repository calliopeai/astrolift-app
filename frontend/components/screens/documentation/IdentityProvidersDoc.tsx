import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

import type { IdentityProviderKind } from "./identity-provider-kinds";

export type IdentityProvidersDocProps = {
  kinds: IdentityProviderKind[];
};

/** Static "Identity providers" documentation page. */
export function IdentityProvidersDoc({ kinds }: IdentityProvidersDocProps) {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Identity providers</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Route sign-in through your corporate IdP: OIDC, SAML, Cognito, Auth0, Okta, Azure AD,
          Google Workspace, GitHub, or local accounts.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          You want users to sign in with their existing corporate identity instead of a separate
          Astrolift password, and you want central control over who has access (deprovision in the
          IdP, lose access here). Exactly one provider is active at a time; the rest are standby.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Prerequisites</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Permission.</strong> An operator role with{" "}
            <code>org.update</code> (Owner and Admin have it).
          </li>
          <li>
            <strong className="text-foreground">Admin access to the IdP.</strong> You will register
            a new OIDC or SAML client there.
          </li>
          <li>
            <strong className="text-foreground">Astrolift&apos;s redirect URI.</strong> Visible in
            the <strong>New provider</strong> dialog. Format is{" "}
            <code>https://&lt;your-install&gt;/app/auth1/callback</code>. Every IdP needs this exact
            value in its allowed callback list.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Configure by provider kind</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Pick the matching subsection. Every kind ends with the same three clicks in Astrolift:{" "}
          <Link
            href="/settings/identity-provider"
            className="text-foreground underline-offset-2 hover:underline"
          >
            Settings · Identity providers
          </Link>{" "}
          → <strong>New provider</strong> → pick the kind → paste the credentials →{" "}
          <strong>Set active</strong>.
        </p>
      </section>

      {kinds.map((k) => (
        <section key={k.id} id={k.id} className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-medium">{k.title}</h3>
            <Badge variant="outline">{k.badge}</Badge>
          </div>
          <p className="text-muted-foreground text-sm leading-relaxed">{k.intro}</p>
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
          By default, an SSO sign-in succeeds only if Astrolift already has an account for that
          user. To let new users self-onboard, mark their email domains as trusted.
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
            Scroll to the <strong>Trusted email domains</strong> card and add <code>corp.com</code>{" "}
            (no <code>@</code>).
          </li>
          <li className="list-decimal leading-relaxed">
            Save. Anyone who signs in via SSO with an <code>@corp.com</code> email is auto-joined to
            the organization with the default member role.
          </li>
        </ol>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">&ldquo;No IdP configured.&rdquo;</strong> The
            install&apos;s <code>bootstrap_idp</code> environment variables were not set. Configure
            a provider from{" "}
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
            <code>APP_BASE_URL</code> in the backend and the redirect URI registered at the IdP must
            match exactly — protocol, host, path, and trailing slash.
          </li>
          <li>
            <strong className="text-foreground">Auto-signup not happening.</strong> The user&apos;s
            email domain isn&apos;t in the trusted list. See{" "}
            <Link
              href="#domain-allowlist"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Domain allowlist
            </Link>{" "}
            above.
          </li>
          <li>
            <strong className="text-foreground">SAML signature verification fails.</strong> The IdP
            rotated its signing certificate. Re-paste the metadata URL (or upload the fresh XML) —
            Astrolift refreshes the trust store on save.
          </li>
        </ul>
      </section>
    </article>
  );
}
