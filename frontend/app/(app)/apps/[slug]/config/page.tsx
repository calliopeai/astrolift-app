import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ConfigEditorClient } from "./config-editor-client";

export const metadata = { title: "Config · App · Astrolift" };

export default async function AppConfigPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <ConfigEditorClient slug={slug} />
    </PreloadQuery>
  );
}
