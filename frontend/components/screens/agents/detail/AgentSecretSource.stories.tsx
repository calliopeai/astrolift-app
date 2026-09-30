import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { AgentSecretBundleReadDenied, AgentSecretSource } from "./AgentSecretSource";

const options = [
  {
    id: "spec-shared",
    slug: "shared-runtime",
    name: "Shared Runtime",
    teamId: null,
    projectId: null,
  },
  {
    id: "spec-project",
    slug: "project-runtime",
    name: "Project Runtime",
    teamId: "team",
    projectId: "project",
  },
];
const meta: Meta<typeof AgentSecretSource> = {
  title: "Screens/Agents/Detail/AgentSecretSource",
  component: AgentSecretSource,
  args: {
    access: "ready",
    options,
    optionsLoading: false,
    optionsError: null,
    search: "",
    onSearch: () => {},
    page: 1,
    totalCount: 2,
    pageSize: 20,
    onPage: () => {},
    onRetryOptions: () => {},
    selection: null,
    onSelect: () => {},
    onClear: () => {},
    sourceState: "unselected",
    onVerify: () => {},
    canListSecrets: true,
    children: <p>Confirmed recipe values and bundles.</p>,
  },
};
export default meta;
type Story = StoryObj<typeof AgentSecretSource>;
export const Unselected: Story = {};
export const Empty: Story = { args: { options: [], totalCount: 0 } };
export const Loading: Story = { args: { optionsLoading: true } };
export const CheckingAccess: Story = { args: { access: "loading" } };
export const Denied: Story = { args: { access: "denied" } };
export const Error: Story = { args: { optionsError: "Network error" } };
export const CheckingSource: Story = { args: { selection: options[0], sourceState: "loading" } };
export const Unavailable: Story = { args: { selection: options[0], sourceState: "unavailable" } };
export const SourceError: Story = { args: { selection: options[0], sourceState: "error" } };
export const Confirmed: Story = { args: { selection: options[0], sourceState: "confirmed" } };
export const SecretListDenied: Story = {
  args: { selection: options[0], sourceState: "confirmed", canListSecrets: false },
};
export const LongStrings: Story = {
  args: {
    options: [
      {
        ...options[0],
        name: "Long Environment Spec Name ".repeat(10),
        slug: "long-runtime-".repeat(9),
      },
    ],
    selection: {
      ...options[0],
      name: "Long Environment Spec Name ".repeat(10),
      slug: "long-runtime-".repeat(9),
    },
    sourceState: "confirmed",
    totalCount: 100000,
    page: 12,
  },
};

export const BundleReadDenied: Story = { render: () => <AgentSecretBundleReadDenied /> };
