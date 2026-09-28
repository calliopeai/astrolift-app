"use client";

import {
  DefinitionBuildView,
  DefinitionObserveView,
  DefinitionRunPanelView,
  DefinitionRunView,
  DefinitionWorkflowScreen,
} from "@/components/screens/workflows/detail/DefinitionWorkflow";
import {
  useDefinitionObserve,
  useDefinitionRun,
  useDefinitionRunPanel,
} from "@/components/screens/workflows/detail/use-definition-workflow";
import { useWorkflowDefinition } from "@/graphql/workflows/tiered.hooks";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
} from "@/graphql/workflows/tiered.types";

import { WorkflowRunDag } from "./workflow-run-dag";
import { WorkflowTabs } from "./workflow-tabs";

/**
 * A repository workflow definition's pillar. The screen owns the markup; each
 * pillar with data of its own gets a container here so only the shown one
 * runs its hooks.
 */
export function DefinitionWorkflowPillar({
  slug,
  pillar,
}: {
  slug: string;
  pillar: "build" | "run" | "observe";
}) {
  const { definition, loading, error } = useWorkflowDefinition(slug);

  return (
    <DefinitionWorkflowScreen
      slug={slug}
      definition={definition}
      loading={loading}
      error={error}
      tabs={definition ? <WorkflowTabs workflowSlug={definition.slug} /> : null}
    >
      {definition &&
        (pillar === "build" ? (
          <DefinitionBuildView definition={definition} />
        ) : pillar === "run" ? (
          <DefinitionRun definition={definition} />
        ) : (
          <DefinitionObserve definition={definition} />
        ))}
    </DefinitionWorkflowScreen>
  );
}

function DefinitionRun({ definition }: { definition: WorkflowDefinitionSummary }) {
  return <DefinitionRunView definition={definition} {...useDefinitionRun(definition)} />;
}

function DefinitionObserve({ definition }: { definition: WorkflowDefinitionSummary }) {
  const observe = useDefinitionObserve(definition);
  return (
    <DefinitionObserveView
      {...observe}
      renderRunPanel={(run) => (
        <DefinitionRunPanel definition={definition} run={run} onRefresh={observe.refetch} />
      )}
    />
  );
}

function DefinitionRunPanel({
  definition,
  run,
  onRefresh,
}: {
  definition: WorkflowDefinitionSummary;
  run: WorkflowDefinitionRun | null;
  onRefresh: () => unknown;
}) {
  const panel = useDefinitionRunPanel(run, onRefresh);
  return (
    <DefinitionRunPanelView
      {...panel}
      run={run}
      runDag={
        run ? (
          <WorkflowRunDag
            key={run.guid}
            workflowId={run.temporalWorkflowId}
            runId={run.temporalRunId}
            definitionSlug={definition.slug}
            isTerminal={panel.terminal}
          />
        ) : null
      }
    />
  );
}
