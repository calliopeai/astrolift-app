import { PreloadQuery } from "@/lib/apollo";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";

import { EnvironmentsClient } from "@/app/(app)/environments/environments-client";

import { AppTabs } from "../components/app-tabs";

export const metadata = { title: "Environments · App · Astrolift" };

export default async function AppEnvironmentsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
      <EnvironmentsClient
        appSlug={slug}
        tabs={<AppTabs slug={slug} active="deployments" />}
      />
    </PreloadQuery>
  );
}
