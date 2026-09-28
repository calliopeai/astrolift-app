"use client";

import { useGoldenSignals } from "@/components/observability/use-golden-signals";
import { FunctionHomeScreen } from "@/components/screens/apps/homes/FunctionHome";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { AppTabs } from "../app-tabs";

interface FunctionHomeProps {
  slug: string;
  name: string;
  workload: AstroliftWorkload;
  /** Public host when the function is HTTP-triggered; empty otherwise. */
  host: string | null;
  /** Active environment for the golden-signals query. */
  environmentName?: string | null;
}

export function FunctionHome({ slug, name, workload, host, environmentName }: FunctionHomeProps) {
  const goldenSignals = useGoldenSignals(slug, environmentName, workload.slug);
  return (
    <FunctionHomeScreen
      name={name}
      workload={workload}
      host={host}
      goldenSignals={goldenSignals}
      tabs={<AppTabs slug={slug} active="overview" />}
    />
  );
}
