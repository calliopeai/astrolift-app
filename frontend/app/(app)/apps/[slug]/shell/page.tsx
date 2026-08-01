import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ShellClient } from "./shell-client";

export const metadata = {
  title: "Shell · Astrolift",
};

export default async function AppShellPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <ShellClient slug={slug} />
    </PreloadQuery>
  );
}
