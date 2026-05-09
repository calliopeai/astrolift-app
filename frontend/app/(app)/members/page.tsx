import {
  LIST_INVITATIONS,
  LIST_MEMBERS,
  LIST_ROLES,
  LIST_ROLE_BINDINGS,
} from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { MembersClient } from "./members-client";

export const metadata = {
  title: "Members · Astrolift",
};

export default function MembersPage() {
  return (
    <PreloadQuery query={LIST_MEMBERS}>
      <PreloadQuery query={LIST_ROLE_BINDINGS}>
        <PreloadQuery query={LIST_ROLES}>
          <PreloadQuery query={LIST_INVITATIONS}>
            <MembersClient />
          </PreloadQuery>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
