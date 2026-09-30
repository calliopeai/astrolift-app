import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import { NextIntlClientProvider, useTranslations } from "next-intl";
import * as React from "react";
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
  serveSecrets,
} from "../list/agents-dispatch-secrets.fixtures";
import { AgentSecretBundlesView } from "../list/AgentSecretBundles";
import { localizedAgentSecretsList } from "./agent-secrets-list";
import { AgentSecretsTab, AgentSecretValues } from "./AgentSecretsTab";

/**
 * The agent's Secrets tab (spec 44 §5.2): its bindings on the embedded list
 * (filtered, sorted and paged by the server) under Values, and the
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
  readError = null,
}: {
  rows: AstroliftAgentSecretStatus[];
  loading?: boolean;
  reveals?: Record<string, string>;
  readError?: string | null;
}) {
  const t = useTranslations("agentSecrets.values");
  const definition = React.useMemo(() => localizedAgentSecretsList(t), [t]);
  const list = useLocalListState(definition);
  const { state } = list;
  const page = serveSecrets(rows, {
    filters: list.filters,
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
  return (
    <AgentSecretValues
      {...SECRETS}
      {...page}
      list={list}
      loading={loading}
      stale={false}
      error={null}
      onRetry={() => {}}
      readError={readError}
      reveals={reveals}
    />
  );
}

function Tab({
  initial = null,
  rows = [...SECRET_ROWS, SECRET_ROW_ERROR],
  loading,
  reveals,
  readError,
}: {
  initial?: string | null;
  rows?: AstroliftAgentSecretStatus[];
  loading?: boolean;
  reveals?: Record<string, string>;
  readError?: string | null;
}) {
  return (
    <AgentSecretsTab
      section={useLocalSettingsSection(initial)}
      values={<Values rows={rows} loading={loading} reveals={reveals} readError={readError} />}
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

/** A binding whose provider refuses the read is the error its row shows. */
export const ProviderError: Story = { render: () => <Tab rows={[SECRET_ROW_ERROR]} /> };

/** The whole read could not answer: no agent cluster, so every ref reads as missing. */
export const StoreUnavailable: Story = {
  render: () => (
    <Tab
      rows={SECRET_ROWS.map((r) => ({ ...r, exists: false }))}
      readError="the organization has no agent cluster"
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(/The secret store could not be read/)
    ).toBeInTheDocument();
  },
};

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

export const GermanLongStrings: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" timeZone="Europe/Berlin" messages={de}>
      <div style={{ width: 768 }}>
        <Tab rows={[LONG_SECRET_ROW, ...SECRET_ROWS]} />
      </div>
    </NextIntlClientProvider>
  ),
};
export const JapaneseBundlesWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" timeZone="Asia/Tokyo" messages={ja}>
      <div style={{ width: 768 }}>
        <Tab initial="bundles" />
      </div>
    </NextIntlClientProvider>
  ),
};
