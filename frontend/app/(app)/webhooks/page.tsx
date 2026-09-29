import { LIST_WEBHOOKS_PAGE } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WebhooksClient } from "./webhooks-client";

export const metadata = { title: "Webhooks · Astrolift" };

/**
 * Preloads the first page so the list paints with rows instead of
 * skeletons. The variables have to be exactly the ones `useWebhooks` sends
 * on first paint (webhooksVariables for the default list state) or the
 * preload is a cache miss: platform-wide scope, no search, the first page
 * size, no cursor.
 */
const FIRST_PAGE = { appSlug: null, search: null, limit: 25, after: null };

export default function WebhooksPage() {
  return (
    <PreloadQuery query={LIST_WEBHOOKS_PAGE} variables={FIRST_PAGE}>
      <WebhooksClient />
    </PreloadQuery>
  );
}
