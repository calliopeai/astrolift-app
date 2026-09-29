import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "@/components/ui/button";

import { accessCrumbs, PEOPLE_HREF } from "./access-nav";
import { PrincipalPage, type PrincipalPageProps } from "./PrincipalPage";
import { personTabs } from "./principal-tabs";

const meta: Meta = {
  title: "Screens/Administration/Access/PrincipalPage",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const BASE: PrincipalPageProps = {
  crumbs: accessCrumbs("people", "grace"),
  principal: { kind: "user", id: "2", name: "grace", detail: "grace@example.com" },
  fallbackTitle: "Member",
  primaryAction: <Button size="sm">Grant access</Button>,
  tabs: personTabs("m-2", "access"),
  loading: false,
  error: null,
  onRetry: () => {},
  notFound: false,
  notFoundCopy: {
    title: "Member not found",
    description: "This member may not exist.",
    backHref: PEOPLE_HREF,
    backLabel: "Back to People",
  },
  children: <p className="text-muted-foreground text-sm">The active tab renders here.</p>,
};

export const Full: Story = { render: () => <PrincipalPage {...BASE} /> };

export const Loading: Story = {
  render: () => <PrincipalPage {...BASE} principal={null} loading />,
};

export const Error: Story = {
  render: () => (
    <PrincipalPage {...BASE} principal={null} error={{ message: "network request failed" }} />
  ),
};

export const NotFound: Story = {
  render: () => <PrincipalPage {...BASE} principal={null} notFound />,
};

export const LongStrings: Story = {
  render: () => (
    <PrincipalPage
      {...BASE}
      crumbs={accessCrumbs("people", "x".repeat(120))}
      principal={{
        kind: "group",
        id: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
        name: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
      }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <PrincipalPage {...BASE} />
    </div>
  ),
};
