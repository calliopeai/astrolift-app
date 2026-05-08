import { LIST_APPS } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppsClient } from "./apps-client";

export const metadata = { title: "Apps · Astrolift" };

export default function AppsPage() {
  return (
    <PreloadQuery query={LIST_APPS}>
      <AppsClient />
    </PreloadQuery>
  );
}
