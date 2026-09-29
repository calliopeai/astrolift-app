"use client";

import {
  ControlsSectionView,
  EnvironmentControlsView,
  WorkloadOpsRowView,
} from "@/components/screens/apps/controls/ControlsSection";
import {
  useControlsSection,
  useEnvironmentControls,
  useWorkloadOps,
} from "@/components/screens/apps/controls/use-controls-section";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

interface Props {
  appSlug: string;
  deployBranch: string;
}

/**
 * Live operational controls (#402). The screen owns the markup; each env
 * row and each workload row runs its own mutation hooks, so each gets a
 * container here.
 */
export function ControlsSection({ appSlug, deployBranch }: Props) {
  const { envs, loading, workloads, workloadsLoading } = useControlsSection(appSlug);
  return (
    <ControlsSectionView
      envs={envs}
      loading={loading}
      renderEnvironment={(env) => (
        <EnvironmentRow
          env={env}
          appSlug={appSlug}
          deployBranch={deployBranch}
          workloads={workloads}
          workloadsLoading={workloadsLoading}
        />
      )}
    />
  );
}

function EnvironmentRow({
  env,
  appSlug,
  deployBranch,
  workloads,
  workloadsLoading,
}: {
  env: AstroliftAppEnvironment;
  appSlug: string;
  deployBranch: string;
  workloads: AstroliftWorkload[];
  workloadsLoading: boolean;
}) {
  return (
    <EnvironmentControlsView
      {...useEnvironmentControls(env, appSlug, deployBranch)}
      workloads={workloads}
      workloadsLoading={workloadsLoading}
      renderWorkload={(w) => <WorkloadRow envName={env.name} workload={w} appSlug={appSlug} />}
    />
  );
}

function WorkloadRow({
  envName,
  workload,
  appSlug,
}: {
  envName: string;
  workload: AstroliftWorkload;
  appSlug: string;
}) {
  return <WorkloadOpsRowView {...useWorkloadOps(envName, workload, appSlug)} />;
}
