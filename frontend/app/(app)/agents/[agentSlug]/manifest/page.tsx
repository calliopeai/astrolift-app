import { ManifestPreviewClient } from "@/app/(app)/apps/[slug]/manifest/manifest-preview-client";

import { AgentAppSurface } from "../components/agent-app-surface";

export const metadata = { title: "Manifest preview · Agent · Astrolift" };

export default async function AgentManifestPreviewPage({
  params,
}: {
  params: Promise<{ agentSlug: string }>;
}) {
  // No PreloadQuery — mirrors the app manifest page: the client fetches both
  // queries directly (adding streams here routinely hangs the dev-mode
  // Suspense boundary in Next.js 16).
  const { agentSlug } = await params;
  return (
    <AgentAppSurface agentSlug={agentSlug}>
      <ManifestPreviewClient slug={agentSlug} />
    </AgentAppSurface>
  );
}
