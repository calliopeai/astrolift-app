import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { BOTH, homeProps, NO_ACCESS, ONLY_AGENTS, ONLY_APPS, OPERATOR } from "./fixtures";
import { HomeScreen, type HomeScreenProps } from "./HomeScreen";
import { withStoryPanels } from "./panels/story-panels";
import type { HomeAccess, HomeLayoutKey } from "./registry";

/**
 * Home per layout (spec 44 §4.3). Panels with a real component are drawn
 * from their fixtures (panels/story-panels.tsx); the rest are still the
 * registry's placeholders. The Layout menu switches in place.
 */
const meta: Meta = { title: "Home/HomeScreen", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

function Live({
  access,
  saved,
  ...rest
}: { access: HomeAccess; saved: HomeLayoutKey | null } & Partial<HomeScreenProps>) {
  const [layout, setLayout] = React.useState(saved);
  const [asked, setAsked] = React.useState(!rest.firstSignIn);
  const props = homeProps(access, layout);
  return (
    <HomeScreen
      {...props}
      panels={withStoryPanels(props.panels)}
      {...rest}
      firstSignIn={!asked}
      onLayoutChange={(key) => {
        setLayout(key);
        setAsked(true);
      }}
    />
  );
}

export const Apps: Story = { render: () => <Live access={BOTH} saved="apps" /> };
export const Agents: Story = { render: () => <Live access={BOTH} saved="agents" /> };
export const Builder: Story = { render: () => <Live access={BOTH} saved="builder" /> };
export const Operator: Story = { render: () => <Live access={OPERATOR} saved="operator" /> };

/** No layout saved: the one question, the access default preselected. */
export const FirstSignIn: Story = {
  render: () => <Live access={BOTH} saved={null} firstSignIn />,
};

/** First sign-in for an operator: all four offered, Operator suggested. */
export const FirstSignInOperator: Story = {
  render: () => <Live access={OPERATOR} saved={null} firstSignIn />,
};

/** Only Apps: Apps is the one layout, so there is no question to ask. */
export const OnlyApps: Story = { render: () => <Live access={ONLY_APPS} saved={null} /> };

export const OnlyAgents: Story = { render: () => <Live access={ONLY_AGENTS} saved={null} /> };

/** A saved layout the person lost access to falls back to the access default. */
export const SavedLayoutNoLongerOffered: Story = {
  render: () => <Live access={ONLY_AGENTS} saved="builder" />,
};

export const Loading: Story = {
  render: () => <Live access={BOTH} saved="builder" loading />,
};

export const NoAccess: Story = { render: () => <Live access={NO_ACCESS} saved={null} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Live access={OPERATOR} saved="builder" />
    </div>
  ),
};

export const FirstSignIn768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Live access={OPERATOR} saved={null} firstSignIn />
    </div>
  ),
};
