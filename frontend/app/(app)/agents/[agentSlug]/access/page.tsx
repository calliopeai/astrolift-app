import { AppMembersClient } from "@/app/(app)/apps/[slug]/members/app-members-client";
import { AppSecurityClient } from "@/app/(app)/apps/[slug]/security/security-client";
import { AppDeployTokensClient } from "@/app/(app)/apps/[slug]/tokens/tokens-client";
import {
  activeAgentSection,
  type SearchParams,
} from "@/components/screens/agents/detail/agent-tabs-model";
import { AgentTabSections } from "@/components/screens/agents/detail/AgentTabSections";
import { LIST_ROLE_BINDINGS, LIST_ROLES } from "@/graphql/identity/identity.queries";
import { LIST_APP_DEPLOY_TOKENS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { PreloadQuery } from "@/lib/apollo";

import { SecureContent } from "../components/secure-content";

export const metadata = { title: "Access · Agent · Astrolift" };

/**
 * The Access tab (spec 44 §5.2, §9 step 5): who may reach and change the
 * agent. Members, Deploy tokens, Security scans, and Guardrails (the former
 * Secure pillar, the Zentinelle gate), one section at a time by
 * `?section=`. `/members`, `/tokens`, `/security` and `/secure` redirect
 * here. Only the active section mounts, and only its queries are preloaded.
 */
export default async function AgentAccessPage({
  params,
  searchParams,
}: {
  params: Promise<{ agentSlug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { agentSlug } = await params;
  const section = activeAgentSection("access", await searchParams);
  return (
    <AgentTabSections slug={agentSlug} tab="access" active={section}>
      {section === "tokens" ? (
        <PreloadQuery query={LIST_APP_DEPLOY_TOKENS} variables={{ appSlug: agentSlug }}>
          <AppDeployTokensClient slug={agentSlug} />
        </PreloadQuery>
      ) : section === "security" ? (
        <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
          <PreloadQuery query={LIST_EVENTS} variables={{ limit: 100 }}>
            <AppSecurityClient slug={agentSlug} />
          </PreloadQuery>
        </PreloadQuery>
      ) : section === "guardrails" ? (
        <SecureContent />
      ) : (
        <PreloadQuery query={GET_APP} variables={{ slug: agentSlug }}>
          <PreloadQuery query={LIST_ROLE_BINDINGS}>
            <PreloadQuery query={LIST_ROLES}>
              <AppMembersClient slug={agentSlug} />
            </PreloadQuery>
          </PreloadQuery>
        </PreloadQuery>
      )}
    </AgentTabSections>
  );
}
