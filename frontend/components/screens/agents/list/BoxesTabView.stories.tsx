import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftAgentBox } from "@/graphql/agents/agents.types";

import { boxesProps, LONG_BOXES } from "./agents-list.fixtures";
import { BoxesTabView } from "./BoxesTabView";

const meta: Meta = { title: "Screens/Agents/List/BoxesTabView" };
export default meta;

type Story = StoryObj;

export const Full: Story = {
  render: () => <BoxesTabView {...boxesProps()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(
        "astro exec --app box-claude-dev-abcd1234 -- tmux new-session -A -s astrolift"
      )
    ).toBeInTheDocument();
    await expect(canvas.getByText("Never")).toBeInTheDocument();
  },
};

export const Loading: Story = {
  render: () => (
    <BoxesTabView
      {...boxesProps([], { controller: fakeController<AstroliftAgentBox>({ state: "loading" }) })}
    />
  ),
};

export const Empty: Story = { render: () => <BoxesTabView {...boxesProps([])} /> };

export const Failed: Story = {
  render: () => (
    <BoxesTabView
      {...boxesProps([], {
        controller: fakeController<AstroliftAgentBox>({
          state: "error",
          error: new globalThis.Error("upstream timed out"),
        }),
      })}
    />
  ),
};

export const ShowingEnded: Story = {
  render: () => <BoxesTabView {...boxesProps(undefined, { includeEnded: true })} />,
};

export const NewBoxDialog: Story = {
  render: () => <BoxesTabView {...boxesProps()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /New agent box/i }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Environment spec")).toBeInTheDocument();
  },
};

/** No environment specs yet: the dialog says to create one first. */
export const NewBoxDialogNoSpecs: Story = {
  render: () => <BoxesTabView {...boxesProps(undefined, { specs: [] })} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /New agent box/i }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText(/No environment specs yet/)).toBeInTheDocument();
  },
};

export const LongStrings: Story = { render: () => <BoxesTabView {...boxesProps(LONG_BOXES)} /> };
