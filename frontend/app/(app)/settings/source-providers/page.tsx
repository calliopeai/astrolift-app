import { redirect } from "next/navigation";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("sourceProviders");

/**
 * Source providers moved into the unified Providers page (#889). The SCM
 * panel (`source-providers-client.tsx`, exported as
 * `SourceProvidersPanel`) now renders under /providers' Source tab; this
 * server-side redirect keeps bookmarks and in-app links (OAuth callbacks
 * pass return_to here historically) working.
 */
export default function SourceProvidersAliasPage() {
  redirect("/providers#source");
}
