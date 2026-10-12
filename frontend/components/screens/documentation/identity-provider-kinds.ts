/** One provider kind documented on the Identity providers page. */
export type IdentityProviderKind = {
  id: string;
  title: string;
  badge: string;
  intro: string;
  steps: string[];
};

/**
 * The built-in kinds. Copy lives under `docsIdentityProviders.kinds.<key>`
 * (`title`, `intro`, `steps.s1`…, and `badge` when it is a word rather than a
 * protocol name); `values` fill a step's URL placeholders.
 */
export type IdentityProviderKindKey = {
  id: string;
  key: string;
  /** Protocol label shown as is; omitted when the badge is translated. */
  badge?: string;
  steps: { key: string; values?: Record<string, string> }[];
};

const THREE_STEPS = [{ key: "s1" }, { key: "s2" }, { key: "s3" }];

export const IDENTITY_PROVIDER_KINDS: IdentityProviderKindKey[] = [
  { id: "oidc", key: "oidc", badge: "OIDC", steps: THREE_STEPS },
  {
    id: "cognito",
    key: "cognito",
    badge: "OIDC",
    steps: [
      { key: "s1" },
      {
        key: "s2",
        values: {
          url: "https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/openid-configuration",
        },
      },
      { key: "s3" },
    ],
  },
  {
    id: "auth0",
    key: "auth0",
    badge: "OIDC",
    steps: [
      { key: "s1" },
      {
        key: "s2",
        values: { url: "https://<your-tenant>.auth0.com/.well-known/openid-configuration" },
      },
      { key: "s3" },
    ],
  },
  { id: "okta", key: "okta", badge: "OIDC", steps: THREE_STEPS },
  {
    id: "azure-ad",
    key: "azureAd",
    badge: "OIDC",
    steps: [
      { key: "s1" },
      { key: "s2" },
      {
        key: "s3",
        values: {
          url: "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration",
        },
      },
    ],
  },
  {
    id: "google",
    key: "google",
    badge: "OIDC",
    steps: [
      { key: "s1" },
      {
        key: "s2",
        values: { url: "https://accounts.google.com/.well-known/openid-configuration" },
      },
      { key: "s3" },
    ],
  },
  { id: "github", key: "github", badge: "OAuth", steps: THREE_STEPS },
  { id: "saml", key: "saml", badge: "SAML", steps: THREE_STEPS },
  { id: "local", key: "local", steps: [{ key: "s1" }, { key: "s2" }] },
];
