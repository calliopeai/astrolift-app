import {
  LIST_ROLES,
  LIST_ROLE_BINDINGS,
} from "@/graphql/identity/identity.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { AppMembersClient } from "./app-members-client";

export const metadata = { title: "Members · App · Astrolift" };

export default async function AppMembersPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery query={GET_APP} variables={{ slug }}>
      <PreloadQuery query={LIST_ROLE_BINDINGS}>
        <PreloadQuery query={LIST_ROLES}>
          <AppMembersClient slug={slug} />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
