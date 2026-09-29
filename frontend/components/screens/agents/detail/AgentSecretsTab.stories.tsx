import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import {
  BUNDLES,
  LONG_SECRET_ROW,
  SECRET_ROW_ERROR,
  SECRET_ROWS,
  SECRETS,
} from "../list/agents-dispatch-secrets.fixtures";
import { AgentSecretBundlesView } from "../list/AgentSecretBundles";
import { agentSecretsList, secretProviders, selectSecrets } from "./agent-secrets-list";
import { AgentSecretsTab, AgentSecretValues } from "./AgentSecretsTab";

/**
 * The agent's Secrets tab (spec 44 §5.2): its bindings on the embedded list
 * (filtered, sorted and paged in the browser) under Values, and the
 * reusable bundles under Bundles, one section at a time.
 */
const meta: Meta = {
  title: "Screens/Agents/Detail/AgentSecretsTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Values({
  rows,
  loading = false,
  reveals = {},
}: {
  rows: AstroliftAgentSecretStatus[];
  loading?: boolean;
  reveals?: Record<string, string>;
}) {
  const list = useLocalListState(agentSecretsList(secretProviders(rows)));
  const { state } = list;
  const page = selectSecrets(rows, list.filters, state.q, state.sort, state.page, state.pageSize);
  return (
    <AgentSecretValues {...SECRETS} {...page} list={list} loading={loading} reveals={reveals} />
  );
}

function Tab({
  initial = null,
  rows = [...SECRET_ROWS, SECRET_ROW_ERROR],
  loading,
  reveals,
}: {
  initial?: string | null;
  rows?: AstroliftAgentSecretStatus[];
  loading?: boolean;
  reveals?: Record<string, string>;
}) {
  return (
    <AgentSecretsTab
      section={useLocalSettingsSection(initial)}
      values={<Values rows={rows} loading={loading} reveals={reveals} />}
      bundles={<AgentSecretBundlesView {...BUNDLES} />}
    />
  );
}

/** Set, missing and failing bindings; the nav leads to Bundles. */
export const Full: Story = {
  render: () => <Tab />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Error")).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Add binding" }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Add a secret binding")).toBeInTheDocument();
  },
};

/** A value revealed by the audited Reveal: shown under its variable until it hides. */
export const Revealed: Story = {
  render: () => <Tab reveals={{ [SECRET_ROWS[0]!.envVar]: "sk-live-example-not-a-real-key" }} />,
};

export const BundlesSection: Story = { render: () => <Tab initial="bundles" /> };

export const Loading: Story = { render: () => <Tab rows={[]} loading /> };

export const Empty: Story = {
  render: () => <Tab rows={[]} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("No secret bindings")).toBeInTheDocument();
  },
};

/**
 * The status read has no error of its own (a failed read comes back empty);
 * a binding whose provider refuses the read is the error the list shows.
 */
export const ProviderError: Story = { render: () => <Tab rows={[SECRET_ROW_ERROR]} /> };

export const LongStrings: Story = {
  render: () => <Tab rows={[LONG_SECRET_ROW, ...SECRET_ROWS]} />,
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Tab rows={[LONG_SECRET_ROW, ...SECRET_ROWS]} />
    </div>
  ),
};
