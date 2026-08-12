import { AgentSecretsDialog } from "../../agent-secrets-dialog";
import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Secrets · Agent · Astrolift" };

export default async function AgentSecretsPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  const { agentSlug } = await params;
  return (
    <AgentAppSurface agentSlug={agentSlug}>
      <AgentSecretsDialog
        envSpecSlug={agentSlug}
        envSpecName={agentSlug}
        managedModel={false}
        vncEnabled={false}
        embedded
        showRuntimeSettings={false}
        open
      />
    </AgentAppSurface>
  );
}
