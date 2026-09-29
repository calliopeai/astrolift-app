import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, SHA64, SKILL } from "./agent-skills.fixtures";
import { SkillFrame } from "./SkillFrame";

const meta: Meta = {
  title: "Screens/Agents/Skills/SkillFrame",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const body = <p className="text-muted-foreground text-sm">The tab&apos;s body.</p>;

export const Builder: Story = {
  render: () => (
    <SkillFrame id="sk-1" active="builder" skill={SKILL} loading={false} error={null}>
      {body}
    </SkillFrame>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Builder" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expect(canvas.getByRole("link", { name: "Skills" })).toHaveAttribute(
      "href",
      "/agents/skills"
    );
  },
};

export const Tools: Story = {
  render: () => (
    <SkillFrame
      id="sk-1"
      active="tools"
      skill={{ ...SKILL, isActive: false, isGlobal: true }}
      loading={false}
      error={null}
    >
      {body}
    </SkillFrame>
  ),
};

export const Loading: Story = {
  render: () => (
    <SkillFrame id="sk-1" active="builder" skill={null} loading error={null}>
      {body}
    </SkillFrame>
  ),
};

export const NotFound: Story = {
  render: () => (
    <SkillFrame id="sk-1" active="builder" skill={null} loading={false} error={null}>
      {body}
    </SkillFrame>
  ),
};

export const LoadError: Story = {
  render: () => (
    <SkillFrame
      id="sk-1"
      active="builder"
      skill={null}
      loading={false}
      error="Response not successful: Received status code 500"
      onRetry={() => {}}
    >
      {body}
    </SkillFrame>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <SkillFrame
        id="sk-1"
        active="builder"
        skill={{ ...SKILL, name: `Document Summariser ${LONG}`, slug: SHA64 }}
        loading={false}
        error={null}
      >
        {body}
      </SkillFrame>
    </div>
  ),
};
