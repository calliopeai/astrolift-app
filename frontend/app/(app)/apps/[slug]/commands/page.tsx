import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { CommandRunnerClient } from "./command-runner-client";

export const metadata = { title: "Run command · App · Astrolift" };

export default async function AppCommandsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={LIST_WORKLOADS} variables={{ appSlug: slug }}>
      <CommandRunnerClient slug={slug} />
    </PreloadQuery>
  );
}
