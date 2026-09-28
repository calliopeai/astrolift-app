"use client";

import { usePathname } from "next/navigation";

import { AgentsSubnavView } from "@/components/screens/agents/list/AgentsSubnavView";

/**
 * Section subnav for Agents / Skills / Tools (#915). The view lives in
 * components/; this keeps the import path the layout uses.
 */
export function AgentsSubnav() {
  return <AgentsSubnavView pathname={usePathname()} />;
}
