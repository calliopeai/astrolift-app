"use client";

import { ToolRegistryScreen } from "@/components/screens/agents/tools/ToolRegistryScreen";
import { useToolRegistry } from "@/components/screens/agents/tools/use-tool-registry";

export default function ToolRegistryPage() {
  const registry = useToolRegistry();
  return <ToolRegistryScreen {...registry} />;
}
