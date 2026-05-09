import { LIST_APP_DEPLOY_TOKENS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppDeployTokensClient } from "./tokens-client";

export const metadata = { title: "Deploy tokens · App · Astrolift" };

export default async function AppDeployTokensPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_APP_DEPLOY_TOKENS} variables={{ appSlug: slug }}>
      <AppDeployTokensClient slug={slug} />
    </PreloadQuery>
  );
}
