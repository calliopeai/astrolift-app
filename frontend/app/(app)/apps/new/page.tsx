import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WizardClient } from "./wizard-client";

export const metadata = { title: "Register app · Astrolift" };

export default function RegisterAppPage() {
  return (
    <PreloadQuery query={LIST_SOURCE_CONNECTIONS}>
      <PreloadQuery query={LIST_PROJECTS}>
        <PreloadQuery query={LIST_TEAMS}>
          <WizardClient />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
