import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";
import type { AstroliftAgentSkill } from "@/graphql/agents/agents.types";

import { BUILD, BUILD_LONG } from "./agent-build-run.fixtures";
import {
  AGENT_SKILLS_LIST,
  agentToolsList,
  selectSkills,
  selectTools,
  toolAdapters,
  toolRows,
} from "./agent-skills-list";
import { AgentSkillsList, AgentToolsList } from "./AgentSkillsTools";

/**
 * The agent's Skills & tools tab (spec 44 §5.2): Skills and Tools, one
 * section each, both on the embedded list with client-side filter, sort and
 * paging (the agent read returns every binding at once).
 */
const meta: Meta = {
  title: "Screens/Agents/Detail/AgentSkillsTools",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type State = { loading?: boolean; error?: { message: string } | null };

function Skills({
  skills,
  loading = false,
  error = null,
}: { skills: AstroliftAgentSkill[] } & State) {
  const list = useLocalListState(AGENT_SKILLS_LIST);
  const { state } = list;
  const page = selectSkills(skills, list.filters, state.q, state.sort, state.page, state.pageSize);
  return (
    <AgentSkillsList list={list} {...page} loading={loading} error={error} onRetry={() => {}} />
  );
}

function Tools({
  skills,
  loading = false,
  error = null,
}: { skills: AstroliftAgentSkill[] } & State) {
  const all = toolRows(skills);
  const list = useLocalListState(agentToolsList(toolAdapters(all)));
  const { state } = list;
  const page = selectTools(all, list.filters, state.q, state.sort, state.page, state.pageSize);
  return (
    <AgentToolsList list={list} {...page} loading={loading} error={error} onRetry={() => {}} />
  );
}

/** Three skills in binding order: one inactive, one without tools. */
export const SkillsFull: Story = {
  render: () => <Skills skills={BUILD.skills} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/paged in the browser/)).toBeInTheDocument();
  },
};
export const SkillsLoading: Story = { render: () => <Skills skills={[]} loading /> };
export const SkillsEmpty: Story = { render: () => <Skills skills={[]} /> };
export const SkillsError: Story = {
  render: () => (
    <Skills skills={[]} error={{ message: "agent(orgId, slug): upstream timed out after 30s" }} />
  ),
};
/** A 96-character skill name and slug, and an unbroken description. */
export const SkillsLongStrings: Story = { render: () => <Skills skills={BUILD_LONG.skills} /> };

/** Every tool the skills carry, with the skill it comes from. */
export const ToolsFull: Story = { render: () => <Tools skills={BUILD.skills} /> };
export const ToolsLoading: Story = { render: () => <Tools skills={[]} loading /> };
export const ToolsEmpty: Story = { render: () => <Tools skills={[]} /> };
export const ToolsError: Story = {
  render: () => (
    <Tools skills={[]} error={{ message: "agent(orgId, slug): upstream timed out after 30s" }} />
  ),
};
/** Long names, an unknown adapter, and a long handler ref. */
export const ToolsLongStrings: Story = { render: () => <Tools skills={BUILD_LONG.skills} /> };

/** The narrowest the web console goes: the table scrolls inside its frame. */
export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Tools skills={BUILD_LONG.skills} />
    </div>
  ),
};
