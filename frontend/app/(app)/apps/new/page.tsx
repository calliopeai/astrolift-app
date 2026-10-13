import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { WizardClient } from "./wizard-client";

export const generateMetadata = routeTitle("newApp");

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
