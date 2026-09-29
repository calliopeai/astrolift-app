import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ADD_TOOL, LONG, LONG_URL, SKILL } from "./agent-skills.fixtures";
import { AddToolScreen } from "./AddToolScreen";

const meta: Meta = {
  title: "Screens/Agents/Skills/AddToolScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Tool: Story = { render: () => <AddToolScreen {...ADD_TOOL} /> };

export const Handler: Story = { render: () => <AddToolScreen {...ADD_TOOL} initialStep={2} /> };

export const Schemas: Story = { render: () => <AddToolScreen {...ADD_TOOL} initialStep={3} /> };

/** The skill is still loading: the crumb falls back to "Skill". */
export const SkillLoading: Story = {
  render: () => <AddToolScreen {...ADD_TOOL} skill={null} skillLoading />,
};

export const Registering: Story = {
  render: () => <AddToolScreen {...ADD_TOOL} initialStep={3} creating />,
};

/** A schema that does not parse: the reason beside its field. */
export const SchemaError: Story = {
  render: () => (
    <AddToolScreen
      {...ADD_TOOL}
      initialStep={3}
      initialErrors={{ inputSchema: "Input schema is not valid JSON." }}
    />
  ),
};

/** A refusal the server tied to a field. */
export const FieldError: Story = {
  render: () => (
    <AddToolScreen
      {...ADD_TOOL}
      initialErrors={{ slug: `A tool with slug ${LONG} already exists on this skill.` }}
    />
  ),
};

export const FormError: Story = {
  render: () => (
    <AddToolScreen
      {...ADD_TOOL}
      initialStep={3}
      initialErrors={{ form: `Handler ${LONG_URL} is not reachable from the cluster.` }}
    />
  ),
};

/** Continue with nothing filled in: the errors appear in place, not in a toast. */
export const ValidatesInPlace: Story = {
  render: () => <AddToolScreen {...ADD_TOOL} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(await canvas.findByText("Give the tool a name.")).toBeInTheDocument();
    await expect(canvas.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
  },
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <AddToolScreen {...ADD_TOOL} skill={{ ...SKILL, name: `Document Summariser ${LONG}` }} />
    </div>
  ),
};
