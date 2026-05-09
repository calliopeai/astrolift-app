import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_APP_SECRETS } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SecretsClient } from "./secrets-client";

export const metadata = { title: "Secrets · App · Astrolift" };

export default async function AppSecretsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
      <PreloadQuery
        query={LIST_APP_SECRETS}
        variables={{ appSlug: slug, environmentName: null }}
      >
        <SecretsClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
