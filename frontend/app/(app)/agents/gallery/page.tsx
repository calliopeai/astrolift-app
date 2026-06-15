"use client";

import Link from "next/link";

import { AgentTheatre } from "@/components/observability/AgentTheatre";
import { PageShell } from "@/components/PageShell";

/**
 * Agent theatre gallery — snapshot tiles for every running watchable
 * agent that explode into a live session. The same {@link AgentTheatre}
 * surface is embedded as the "Theatre" tab under /observe/agents; this
 * dedicated route is the deep-linkable home for the gallery.
 */
export default function AgentGalleryPage() {
  return (
    <PageShell
      title="Agent theatre"
      description="Live snapshot tiles for every running watchable agent. Click a tile to explode it into a full session."
    >
      <AgentTheatre />
      <div className="mt-2">
        <Link
          href="/agents"
          className="text-muted-foreground hover:text-foreground text-sm underline-offset-2 hover:underline"
        >
          ← Back to Agents
        </Link>
      </div>
    </PageShell>
  );
}
