import { activeSection, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";
import { LIST_APP_DEPLOY_TOKENS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppAccessClient } from "./access-client";

export const metadata = { title: "Access · App · Astrolift" };

/**
 * The Access tab (spec 44 §5.2, §10.3): who may reach and change the app.
 * People with access, Deploy tokens, Security scans and Edge access (the central-auth
 * rule in front of the app), one section at a time by `?section=`, on the
 * settings archetype. `/members`, `/tokens` and `/security` redirect here.
 * Only the active section's queries are preloaded.
 */
export default async function AppAccessPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  const section = activeSection("access", await searchParams);
  return section === "tokens" ? (
    // The first page the tokens list asks for (use-deploy-tokens, use-cursor-list).
    <PreloadQuery
      query={LIST_APP_DEPLOY_TOKENS_PAGE}
      variables={{ appSlug: slug, search: null, limit: 25, after: null }}
    >
      <AppAccessClient slug={slug} />
    </PreloadQuery>
  ) : section === "security" ? (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_EVENTS} variables={{ limit: 100 }}>
        <AppAccessClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  ) : section === "edge" ? (
    <AppAccessClient slug={slug} />
  ) : (
    // People with access reads its rows through the list's URL state (view,
    // search, cursor), so only the app is preloaded here.
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <AppAccessClient slug={slug} />
    </PreloadQuery>
  );
}
