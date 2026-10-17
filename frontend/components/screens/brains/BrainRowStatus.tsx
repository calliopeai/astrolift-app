"use client";

import { type BrainStatusPart, BrainStatusCell } from "./BrainStatusCell";
import { statusEnvironment } from "./brain-status-model";
import { useBrainStatus } from "./use-brain-status";

/**
 * A list row's status column with its own read: each visible row asks for
 * its status once (the three columns share the one cached answer), so a
 * page of ten rows is ten reads and rows on other pages cost nothing.
 */
export function BrainRowStatus({
  slug,
  environments,
  part,
}: {
  slug: string;
  environments: readonly { name: string }[];
  part: BrainStatusPart;
}) {
  const environment = statusEnvironment(environments);
  const { result } = useBrainStatus(slug, environment);
  return <BrainStatusCell part={part} result={result} environment={environment} />;
}
