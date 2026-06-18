import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import { PreloadQuery } from "@/lib/apollo";

import { WizardClient } from "./wizard-client";

export const metadata = { title: "Register agent repo · Astrolift" };

/**
 * Register-agent-repo wizard (spec 33 PR-8).
 *
 * Cloned from the register-app wizard (`apps/new`) with the manifest /
 * app-details / deploy-strategy steps swapped for a single scan +
 * multi-agent review step: point at a repo, discover its agent manifests
 * (`scanAgentManifests`), pick the destination project, then register the
 * whole repo's agents (`registerAgentRepo`) — each manifest becomes an agent
 * Workload under its own RegisteredApp and surfaces on the `/agents` list.
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
