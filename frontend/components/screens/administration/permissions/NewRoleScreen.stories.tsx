import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { LONG_ROLE, newRoleProps, OWNER_ROLE, VIEWER_ROLE } from "./fixtures";
import { NewRoleScreen } from "./NewRoleScreen";

const meta: Meta = {
  title: "Screens/Administration/Permissions/NewRoleScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Step 1, a blank role. */
export const Blank: Story = { render: () => <NewRoleScreen {...newRoleProps()} /> };

/** Duplicating a built-in: named, scoped and seeded from it. */
export const Duplicate: Story = {
  render: () => <NewRoleScreen {...newRoleProps({ from: VIEWER_ROLE })} />,
};

/** Step 2: the matrix, diffed against Viewer. */
export const Permissions: Story = {
  render: () => (
    <NewRoleScreen
      {...newRoleProps({ from: VIEWER_ROLE })}
      initialStep={1}
      initialDraft={{
        name: "Viewer plus deploy",
        permissions: ["app.deploy", "app.read", "cluster.read"],
      }}
    />
  ),
};

/** Step 3: what the role will allow, and the diff. */
export const Review: Story = {
  render: () => (
    <NewRoleScreen
      {...newRoleProps({ from: VIEWER_ROLE })}
      initialStep={2}
      initialDraft={{ name: "Viewer plus deploy", permissions: ["app.deploy", "app.read"] }}
    />
  ),
};

export const Creating: Story = {
  render: () => (
    <NewRoleScreen
      {...newRoleProps({ from: VIEWER_ROLE, creating: true })}
      initialStep={2}
      initialDraft={{ name: "Viewer plus deploy" }}
    />
  ),
};

/** A blank name: the error sits under the field and the step does not move. */
export const MissingName: Story = {
  render: () => <NewRoleScreen {...newRoleProps()} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Continue" }));
    await expect(c.getByText("Name the role.")).toBeInTheDocument();
  },
};

/** A slug another role already uses. */
export const TakenSlug: Story = {
  render: () => (
    <NewRoleScreen
      {...newRoleProps()}
      initialDraft={{ name: "Org Owner", slug: OWNER_ROLE.slug }}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Continue" }));
    await expect(c.getByText(/already exists/)).toBeInTheDocument();
  },
};

/** The roles are still loading, so there is nothing to start from yet. */
export const Loading: Story = {
  render: () => <NewRoleScreen {...newRoleProps({ roles: [], loading: true })} />,
};

export const LongStrings: Story = {
  render: () => (
    <NewRoleScreen
      {...newRoleProps({ from: LONG_ROLE })}
      initialStep={2}
      initialDraft={{ name: LONG_ROLE.name }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <NewRoleScreen {...newRoleProps({ from: LONG_ROLE })} />
    </div>
  ),
};
