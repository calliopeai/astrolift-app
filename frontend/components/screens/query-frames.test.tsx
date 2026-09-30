import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ModulesCard } from "./administration/organization/ModulesCard";
import { modules } from "./administration/organization/fixtures";
import { TriggerBindingEditorView } from "./agents/detail/TriggerBindingEditor";
import { EDITOR } from "./agents/detail/agent-trigger-editor.fixtures";
import { SecretProposalDetailScreen } from "./approvals/SecretProposalDetail";
import { SecretProposalsQueue } from "./approvals/SecretProposalsQueue";
import { DETAIL, QUEUE } from "./approvals/approvals-b.fixtures";
import { ManifestPreviewScreen } from "./apps/config/ManifestPreviewScreen";
import { PREVIEW_PROPS } from "./apps/config/app-config-agent.fixtures";
import { AppLogsScreen } from "./apps/deployments/AppLogsScreen";
import { LOGS } from "./apps/deployments/app-deployments-logs.fixtures";
import { ManagedServicesSummaryView } from "./apps/overview/ManagedServicesSummaryCard";
import { DriversScreen } from "./documentation/DriversScreen";
import { DOCS_E_DRIVERS_FALLBACK } from "./documentation/docs-e.fixtures";
import { DeployModelScreen } from "./models/DeployModelScreen";
import { DEPLOY, CLOUD_PROVIDERS } from "./models/models-providers.fixtures";
import { OpsScreen } from "./ops/OpsScreen";
import { OPS_EMPTY } from "./ops/metrics-ops-shell.fixtures";
import { PipelineDetailScreen } from "./pipelines/PipelineDetail";
import { DETAIL as PIPELINE } from "./pipelines/pipelines-previews.fixtures";
import { CloudProvidersPanelView } from "./providers/CloudProvidersPanel";
import { WorkflowFrame } from "./workflows/detail/WorkflowFrame";
import { FRAME } from "./workflows/detail/workflow-frame.fixtures";
import { ConfirmProvider } from "@/hooks/use-confirm";
import messages from "@/messages/en.json";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/test",
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));

const refusal = { name: "Error", message: "The request was refused" };
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages}>
    <MockedProvider mocks={[]}>
      <ConfirmProvider>{children}</ConfirmProvider>
    </MockedProvider>
  </NextIntlClientProvider>
);
const frames: Array<{ name: string; view: (retry: () => void) => ReactNode; empty: RegExp }> = [
  {
    name: "modules",
    view: (retry) => <ModulesCard {...modules} error={refusal} onRetry={retry} />,
    empty: /Enable Chat Studio/,
  },
  {
    name: "trigger bindings",
    view: (retry) => (
      <TriggerBindingEditorView {...EDITOR} triggers={[]} error={refusal} onRetry={retry} />
    ),
    empty: /No triggers bound yet/,
  },
  {
    name: "proposal queue",
    view: (retry) => (
      <SecretProposalsQueue {...QUEUE} proposals={[]} error={refusal} onRetry={retry} />
    ),
    empty: /No pending secret/,
  },
  {
    name: "proposal detail",
    view: (retry) => (
      <SecretProposalDetailScreen {...DETAIL} proposal={null} error={refusal} onRetry={retry} />
    ),
    empty: /not found/i,
  },
  {
    name: "manifest",
    view: (retry) => (
      <ManifestPreviewScreen {...PREVIEW_PROPS} result={null} error={refusal} onRetry={retry} />
    ),
    empty: /App not found/,
  },
  {
    name: "app logs",
    view: (retry) => <AppLogsScreen {...LOGS} app={null} appError={refusal} onRetryApp={retry} />,
    empty: /not found/i,
  },
  {
    name: "services summary",
    view: (retry) => (
      <ManagedServicesSummaryView
        services={[]}
        loading={false}
        error={refusal.message}
        onRetry={retry}
        managedServicesHref="/services"
        dialogs={{
          reveal: () => null,
          sendEmail: () => null,
          objects: () => null,
          depth: () => null,
        }}
      />
    ),
    empty: /No managed services/,
  },
  {
    name: "driver reference",
    view: (retry) => (
      <DriversScreen {...DOCS_E_DRIVERS_FALLBACK} rows={[]} error={refusal} onRetry={retry} />
    ),
    empty: /Static reference/,
  },
  {
    name: "model targets",
    view: (retry) => (
      <DeployModelScreen {...DEPLOY} envs={[]} envsError={refusal.message} onRetryTargets={retry} />
    ),
    empty: /No app environments yet/,
  },
  {
    name: "pipeline runs",
    view: (retry) => (
      <PipelineDetailScreen
        {...PIPELINE}
        runs={[]}
        runsError={refusal}
        onRetryRuns={retry}
        secrets={null}
      />
    ),
    empty: /No runs yet/,
  },
  {
    name: "cloud providers",
    view: (retry) => (
      <CloudProvidersPanelView
        {...CLOUD_PROVIDERS}
        configured={[]}
        error={refusal}
        onRetry={retry}
      />
    ),
    empty: /No cloud providers configured/,
  },
  {
    name: "workflow",
    view: (retry) => (
      <WorkflowFrame {...FRAME} workflow={null} error={refusal.message} onRetry={retry}>
        {null}
      </WorkflowFrame>
    ),
    empty: /Workflow not found/,
  },
];

describe.each(frames)("$name failed frame", ({ view, empty }) => {
  it("shows the request error and a working retry instead of empty or not found", () => {
    const retry = vi.fn();
    render(view(retry), { wrapper });
    expect(screen.getByRole("alert")).toHaveTextContent(refusal.message);
    expect(screen.queryByText(empty)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

it("never reports zero deploys or all clear when the Ops requests failed", () => {
  render(
    <OpsScreen
      {...OPS_EMPTY}
      metrics={undefined}
      clustersError={refusal}
      metricsError={refusal}
      alertsError={refusal}
      runsError={refusal}
      auditError={refusal}
    />,
    { wrapper }
  );
  expect(screen.getAllByRole("alert")).toHaveLength(8);
  expect(screen.queryByText(/0 ok · 0 failed · 0 in flight/)).not.toBeInTheDocument();
  expect(screen.queryByText("All clear")).not.toBeInTheDocument();
  expect(screen.queryByText("No clusters registered")).not.toBeInTheDocument();
});

it("blocks model deployment while GPU capability reads are unavailable", () => {
  render(<DeployModelScreen {...DEPLOY} clusters={[]} clustersError={refusal.message} />, {
    wrapper,
  });
  expect(screen.getByRole("alert")).toHaveTextContent("Could not load cluster GPU capabilities");
  expect(screen.getByRole("button", { name: /Next|Continue/ })).toBeDisabled();
});

it("keeps the manifest render result visible while its environment picker reports an error", () => {
  render(
    <ManifestPreviewScreen {...PREVIEW_PROPS} environments={[]} environmentsError={refusal} />,
    { wrapper }
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Could not load environments");
  expect(screen.getByText("3 resources")).toBeInTheDocument();
  expect(screen.getByRole("combobox")).toBeDisabled();
});

it("blocks keyboard form submission when the cluster capability request failed", () => {
  const deploy = vi.fn();
  render(
    <DeployModelScreen
      {...DEPLOY}
      initialStep={3}
      initialEnvId={DEPLOY.envs[0].id}
      clustersError={refusal.message}
      deploy={deploy}
    />,
    { wrapper }
  );
  const form = screen.getByRole("button", { name: "Deploy" }).closest("form");
  expect(form).not.toBeNull();
  fireEvent.submit(form!);
  expect(deploy).not.toHaveBeenCalled();
});
