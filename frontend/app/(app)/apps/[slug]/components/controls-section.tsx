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
      workloads={workloads}
      workloadsLoading={workloadsLoading}
      renderWorkload={(w) => <WorkloadRow workload={w} />}
      renderEnvironment={(env) => (
        <EnvironmentRow env={env} appSlug={appSlug} deployBranch={deployBranch} />
      )}
    />
  );
}

function EnvironmentRow({
  env,
  appSlug,
  deployBranch,
}: {
  env: AstroliftAppEnvironment;
  appSlug: string;
  deployBranch: string;
}) {
  return <EnvironmentControlsView {...useEnvironmentControls(env, appSlug, deployBranch)} />;
}

function WorkloadRow({ workload }: { workload: AstroliftWorkload }) {
  return <WorkloadOpsRowView {...useWorkloadOps(workload)} />;
}
