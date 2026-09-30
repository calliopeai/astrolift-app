import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { AGENT_REPO_PICKER } from "@/components/screens/agents/new/agents-wizard-b.fixtures";
import { SourceRepositoryPicker } from "./SourceRepositoryPicker";

const meta: Meta<typeof SourceRepositoryPicker> = {
  title: "Wizard/SourceRepositoryPicker",
  component: SourceRepositoryPicker,
  args: {
    ...AGENT_REPO_PICKER,
    repoCounts: AGENT_REPO_PICKER.repoToAgentCount,
    registeredKind: "agent",
    registeredNote: (count) => `This repository already hosts ${count} agents.`,
    missingHostLink: <a href="/providers#source">Connect another source</a>,
    emptyReposLink: <a href="/providers#source">/providers</a>,
    pickedDetails: <p>Pick the ref to scan in the agent wizard.</p>,
  },
};
export default meta;
type Story = StoryObj<typeof SourceRepositoryPicker>;
export const Agent: Story = {};
export const App: Story = {
  args: {
    registeredKind: "app",
    pickedDetails: <p>Deploys from the repository default branch.</p>,
  },
};
export const Loading: Story = { args: { reposLoading: true } };
export const Empty: Story = {
  args: {
    repoList: { repos: [], errorCode: null, errorMessage: null, recoverable: false },
    sourceRepo: "",
  },
};
