import { PreloadQuery } from "@/lib/apollo";
import { LIST_PREVIEW_ENVIRONMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";

import { PreviewsClient } from "./previews-client";

export const metadata = { title: "Preview environments · Astrolift" };

/**
 * Preloads the first page the list asks for (see `previewsVariables`: no
 * app, no search, 25 rows, no cursor), so it paints with rows. A link that
 * already carries chips or a search fetches on the client.
 */
export default function PreviewsPage() {
  return (
    <PreloadQuery
      query={LIST_PREVIEW_ENVIRONMENTS_PAGE}
      variables={{ appSlug: null, search: null, limit: 25, after: null }}
    >
      <PreviewsClient />
    </PreloadQuery>
  );
}
