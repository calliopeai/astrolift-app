import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WebhooksClient } from "@/app/(app)/webhooks/webhooks-client";

import { AppTabs } from "../components/app-tabs";

export const metadata = { title: "Webhooks · App · Astrolift" };

export default async function AppWebhooksPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_WEBHOOKS} variables={{ appSlug: slug }}>
      <WebhooksClient
        appSlug={slug}
        tabs={<AppTabs slug={slug} active="settings" />}
      />
    </PreloadQuery>
  );
}
