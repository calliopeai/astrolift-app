"use client";

import { useParams } from "next/navigation";

import { EnvironmentSpecScreen } from "@/components/screens/agents/environment-specs/EnvironmentSpecScreen";
import { useEnvironmentSpec } from "@/components/screens/agents/environment-specs/use-environment-specs";

export default function EnvironmentSpecPage() {
  const { slug } = useParams<{ slug: string }>();
  return <EnvironmentSpecScreen {...useEnvironmentSpec(slug)} />;
}
