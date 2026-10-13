import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("identityProviders");

/**
 * Identity providers moved into the unified Providers page (#890). The
 * IdP panel (`identity-provider-client.tsx`, exported as
 * `IdentityProvidersPanel`) now renders under /providers' Identity tab;
 * this server-side redirect keeps bookmarks and in-app links working.
 */
export default function IdentityProviderAliasPage() {
  redirect("/providers#identity");
}
