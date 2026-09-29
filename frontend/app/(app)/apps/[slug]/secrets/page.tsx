import { secretsSection } from "@/components/screens/apps/secrets/secrets-list";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import {
  LIST_APP_SECRET_BUNDLE_ATTACHMENTS,
  LIST_APP_SECRETS,
} from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SecretsClient } from "./secrets-client";

export const metadata = { title: "Secrets · App · Astrolift" };

/**
 * The Secrets tab: the keys, or the attached bundles on `?section=bundles`.
 * Only the section on screen is preloaded (Leo's page rule 2).
 */
export default async function AppSecretsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ section?: string | string[] }>;
}) {
  const { slug } = await params;
  const section = secretsSection((await searchParams).section);
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
      <PreloadQuery
        query={section === "bundles" ? LIST_APP_SECRET_BUNDLE_ATTACHMENTS : LIST_APP_SECRETS}
        variables={{ appSlug: slug, environmentName: null }}
      >
        <SecretsClient slug={slug} section={section} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
