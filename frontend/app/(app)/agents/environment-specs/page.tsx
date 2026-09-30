"use client";

import { EnvironmentSpecsScreen } from "@/components/screens/agents/environment-specs/EnvironmentSpecsScreen";
import { useEnvironmentSpecs } from "@/components/screens/agents/environment-specs/use-environment-specs";

export default function EnvironmentSpecsPage() {
  return <EnvironmentSpecsScreen {...useEnvironmentSpecs()} />;
}
