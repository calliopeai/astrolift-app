"use client";

import { useTranslations } from "next-intl";

import { useRouter } from "next/navigation";
import * as React from "react";

import { useNow } from "@/components/screens/deployments/run-support";

import { agentHref, runningSnapshot } from "./apps-agents-model";
import { combineReads, RUNS_POLL_MS, useHomeAgentRuns, useHomeFleet } from "./home-reads";
import type { RunningNowPanelViewProps } from "./RunningNowPanel";

/** The running runs the manifest shows; the fleet read says how many each agent has. */
const RUNNING_LIMIT = 25;

/**
 * Running now's data: the org's fleet (the Agents list's read) and the
 * running agent runs, polled like the Runs page, as a fleet snapshot of the
 * agents with a run in flight. Picking an agent opens it.
 */
export function useRunningNow(): Omit<RunningNowPanelViewProps, "panel"> {
  const t = useTranslations("home");
  const noProject = t("copy.noProject");
  const router = useRouter();
  const fleet = useHomeFleet();
  const running = useHomeAgentRuns("running", { limit: RUNNING_LIMIT });
  // The clock the views age runs against, read again at the poll's pace.
  const now = useNow(true, RUNS_POLL_MS);
  const snapshot = React.useMemo(
    () => (fleet.loading ? null : runningSnapshot(fleet.agents, running.runs, now, noProject)),
    [fleet.loading, fleet.agents, running.runs, now, noProject]
  );
  const slugById = React.useMemo(
    () => new Map(fleet.agents.map((a) => [a.id, a.slug])),
    [fleet.agents]
  );
  return {
    snapshot,
    ...combineReads([fleet, running], Boolean(snapshot && snapshot.agents.length > 0)),
    onSelectAgent: (id) => {
      const slug = slugById.get(id);
      if (slug) router.push(agentHref(slug));
    },
  };
}
