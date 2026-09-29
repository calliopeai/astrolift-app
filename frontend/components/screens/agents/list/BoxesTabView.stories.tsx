import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { AGENT_BOXES_LIST } from "./agent-boxes-list";
import { boxesProps, LONG_BOXES } from "./agents-list.fixtures";
import { BoxesTabView, type BoxesTabViewProps } from "./BoxesTabView";

const meta: Meta = { title: "Screens/Agents/List/BoxesTabView" };
export default meta;

type Story = StoryObj;

/** The view with its list state in memory, as the hook's URL state would give it. */
function Boxes(props: Omit<BoxesTabViewProps, "list">) {
  const list = useLocalListState(AGENT_BOXES_LIST);
  return <BoxesTabView list={list} {...props} />;
}

export const Full: Story = {
  render: () => <Boxes {...boxesProps()} />,
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
  render: () => <Boxes {...boxesProps([], { loading: true })} />,
};

export const Empty: Story = { render: () => <Boxes {...boxesProps([])} /> };

export const Failed: Story = {
  render: () => <Boxes {...boxesProps([], { error: { message: "upstream timed out" } })} />,
};

export const ShowingEnded: Story = {
  render: () => <Boxes {...boxesProps(undefined, { includeEnded: true })} />,
};

export const NewBoxDialog: Story = {
  render: () => <Boxes {...boxesProps()} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /New agent box/i }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Environment spec")).toBeInTheDocument();
  },
};

/** No environment specs yet: the dialog says to create one first. */
export const NewBoxDialogNoSpecs: Story = {
  render: () => <Boxes {...boxesProps(undefined, { specs: [] })} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /New agent box/i }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText(/No environment specs yet/)).toBeInTheDocument();
  },
};

export const LongStrings: Story = { render: () => <Boxes {...boxesProps(LONG_BOXES)} /> };
