import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WebhooksClient } from "./webhooks-client";

export const metadata = { title: "Webhooks · Astrolift" };

export default function WebhooksPage() {
  return (
    <PreloadQuery query={LIST_WEBHOOKS}>
      <WebhooksClient />
    </PreloadQuery>
  );
}
