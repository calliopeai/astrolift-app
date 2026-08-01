import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { LogsClient } from "./logs-client";

export const metadata = {
  title: "Logs · Astrolift",
};

export default async function AppLogsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <LogsClient slug={slug} />
    </PreloadQuery>
  );
}
