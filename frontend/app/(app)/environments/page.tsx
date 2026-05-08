import { PreloadQuery } from "@/lib/apollo";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";

import { EnvironmentsClient } from "./environments-client";

export const metadata = { title: "Environments · Astrolift" };

export default function EnvironmentsPage() {
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: null }}>
      <EnvironmentsClient />
    </PreloadQuery>
  );
}
