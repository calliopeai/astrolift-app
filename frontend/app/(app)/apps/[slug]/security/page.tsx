import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppSecurityClient } from "./security-client";

export const metadata = { title: "Security · App · Astrolift" };

export default async function AppSecurityPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_EVENTS} variables={{ limit: 100 }}>
        <AppSecurityClient slug={slug} />
      </PreloadQuery>
    </PreloadQuery>
  );
}
