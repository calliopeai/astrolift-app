/** One provider kind documented on the Identity providers page. */
export type IdentityProviderKind = {
  id: string;
  title: string;
  badge: string;
  intro: string;
  steps: string[];
};

export const IDENTITY_PROVIDER_KINDS: IdentityProviderKind[] = [
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
    intro: "Entra (formerly Azure AD) is the right pick if your org is on Microsoft 365.",
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
    intro: "Google Workspace OIDC works when every Astrolift operator has a workspace account.",
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
    intro: "For IdPs that only speak SAML (older enterprise installs, some federated setups).",
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
