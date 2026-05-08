import { PreloadQuery } from "@/lib/apollo";
import { LIST_PREVIEW_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";

import { PreviewsClient } from "./previews-client";

export const metadata = { title: "Preview environments · Astrolift" };

export default function PreviewsPage() {
  return (
    <PreloadQuery query={LIST_PREVIEW_ENVIRONMENTS} variables={{ appSlug: null }}>
      <PreviewsClient />
    </PreloadQuery>
  );
}
