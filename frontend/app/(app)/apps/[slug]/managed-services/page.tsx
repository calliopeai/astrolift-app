import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ManagedServicesClient } from "./managed-services-client";

export const metadata = { title: "Managed services · App · Astrolift" };

export default async function AppManagedServicesPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
      <PreloadQuery
        query={LIST_MANAGED_SERVICES}
        variables={{ appSlug: slug, environmentName: null }}
      >
        <ManagedServicesClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
