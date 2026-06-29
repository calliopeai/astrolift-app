import { redirect } from "next/navigation";

export const metadata = {
  title: "Identity providers · Astrolift",
};

/**
 * Identity providers moved into the unified Providers page (#890). The
 * IdP panel (`identity-provider-client.tsx`, exported as
 * `IdentityProvidersPanel`) now renders under /providers' Identity tab;
 * this server-side redirect keeps bookmarks and in-app links working.
 */
export default function IdentityProviderAliasPage() {
  redirect("/providers#identity");
}
