import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SettingsClient } from "./settings-client";

export const metadata = { title: "Settings · App · Astrolift" };

export default async function AppSettingsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
        <PreloadQuery query={LIST_APP_DOMAINS} variables={{ appSlug: slug }}>
          <SettingsClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
