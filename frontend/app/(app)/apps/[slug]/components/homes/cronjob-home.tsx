"use client";

import { CronjobHomeScreen } from "@/components/screens/apps/homes/CronjobHome";
import { useCronjobRuns } from "@/components/screens/apps/homes/use-cronjob-runs";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

interface CronjobHomeProps {
  slug: string;
  name: string;
  workload: AstroliftWorkload;
}

export function CronjobHome({ slug, name, workload }: CronjobHomeProps) {
  const runs = useCronjobRuns(slug, workload.slug);
  return <CronjobHomeScreen {...runs} name={name} workload={workload} />;
}
