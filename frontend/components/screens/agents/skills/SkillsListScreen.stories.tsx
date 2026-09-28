import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  SKILLS_LIST_EMPTY,
  SKILLS_LIST_ERROR,
  SKILLS_LIST_FULL,
  SKILLS_LIST_LOADING,
  SKILLS_LIST_LONG,
} from "./agent-skills.fixtures";
import { SkillsListScreen } from "./SkillsListScreen";

const meta: Meta = { title: "Screens/Agents/Skills/SkillsListScreen" };
export default meta;

type Story = StoryObj;

export const Loading: Story = { render: () => <SkillsListScreen {...SKILLS_LIST_LOADING} /> };

export const Empty: Story = { render: () => <SkillsListScreen {...SKILLS_LIST_EMPTY} /> };

export const LoadError: Story = { render: () => <SkillsListScreen {...SKILLS_LIST_ERROR} /> };

/** Own skills (active, inactive, no description) and a global skill. */
export const Full: Story = { render: () => <SkillsListScreen {...SKILLS_LIST_FULL} /> };

export const LongStrings: Story = { render: () => <SkillsListScreen {...SKILLS_LIST_LONG} /> };
