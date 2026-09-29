import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WizardClient } from "./wizard-client";

export const metadata = { title: "New agent · Astrolift" };

/**
 * Agents › New agent (spec 33 PR-8, spec 44 §5.4): point at a repo, find its
 * agent manifests (`scanAgentManifests`), pick the destination project, then
 * register the repo's agents (`registerAgentRepo`). Each manifest becomes an
 * agent workload under its own app and shows on the `/agents` list.
 */
export default function RegisterAgentRepoPage() {
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
