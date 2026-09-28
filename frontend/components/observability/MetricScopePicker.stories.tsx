import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { MetricScopePicker } from "@/components/observability/MetricScopePicker";

const meta: Meta = { title: "Patterns/Observability/MetricScopePicker" };
export default meta;

function Demo({ loading = false }: { loading?: boolean }) {
  const [env, setEnv] = React.useState<string | null>("production");
  const [workload, setWorkload] = React.useState<string | null>(null);
  return (
    <MetricScopePicker
      environmentName={env}
      workloadSlug={workload}
      onEnvironmentChange={setEnv}
      onWorkloadChange={setWorkload}
      environments={
        loading
          ? []
          : ([
              { id: "e1", name: "production" },
              { id: "e2", name: "staging" },
            ] as never)
      }
      environmentsLoading={loading}
      workloads={
        loading
          ? []
          : ([
              { id: "w1", name: "web", slug: "web" },
              { id: "w2", name: "worker", slug: "worker" },
            ] as never)
      }
      workloadsLoading={loading}
    />
  );
}

export const Default: StoryObj = { render: () => <Demo /> };
export const Loading: StoryObj = { render: () => <Demo loading /> };
