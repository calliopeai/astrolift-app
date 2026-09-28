import { activeSection, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";
import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppSettingsTabClient } from "./settings-client";

export const metadata = { title: "Settings · App · Astrolift" };

/**
 * The Settings tab (spec 44 §5.2, §5.3) on the settings archetype: General
 * (grouped by concern, each part saving on its own), Configuration (the
 * config editor), Manifest, Webhooks, Environments and one Danger zone, one
 * section at a time by `?section=`. `/config`, `/manifest`, `/webhooks` and
 * `/environments` redirect here. Only the active section's queries are
 * preloaded.
 *
 * Manifest has no `PreloadQuery`: its client fetches both queries itself
 * (more streams hang the dev-mode Suspense boundary in Next.js 16).
 */
export default async function AppSettingsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  const section = activeSection("settings", await searchParams);
  return section === "configuration" || section === "danger-zone" ? (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <AppSettingsTabClient slug={slug} />
    </PreloadQuery>
  ) : section === "manifest" ? (
    <AppSettingsTabClient slug={slug} />
  ) : section === "webhooks" ? (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_WEBHOOKS} variables={{ appSlug: slug }}>
        <AppSettingsTabClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  ) : section === "environments" ? (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <AppSettingsTabClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  ) : (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <PreloadQuery query={LIST_APP_DOMAINS} variables={{ appSlug: slug }}>
          <AppSettingsTabClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
