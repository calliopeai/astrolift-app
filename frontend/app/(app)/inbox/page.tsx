import { Suspense } from "react";

import { routeTitle } from "@/lib/i18n/route-title";

import { AgentInboxClient } from "./agent-inbox-client";

export const generateMetadata = routeTitle("agentInbox");

/** Messages from agents to the signed-in person, answered inline (#2434). */
export default function AgentInboxPage() {
  return (
    <Suspense>
      <AgentInboxClient />
    </Suspense>
  );
}
