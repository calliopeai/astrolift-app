"use client";

import { AgentTheatre } from "@/components/observability/AgentTheatre";
import { useAgentGallery } from "@/components/observability/use-agent-gallery";

/** The theatre wired to the gallery; mounted only while its tab is open, so it polls only then. */
export function AgentTheatreContainer() {
  return <AgentTheatre {...useAgentGallery()} />;
}
