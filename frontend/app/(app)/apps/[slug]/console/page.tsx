import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ConsoleClient } from "./console-client";

export const metadata = {
  title: "Console · Astrolift",
};

export default async function AppConsolePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <ConsoleClient slug={slug} />
    </PreloadQuery>
  );
}
