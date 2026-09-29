import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PlatformIncidentBanner, type StatusIncident } from "@/components/PlatformIncidentBanner";

const meta: Meta = {
  title: "Patterns/Notices/PlatformIncidentBanner",
  parameters: { layout: "fullscreen" },
};
export default meta;

const incident = (id: string, name: string, impact: StatusIncident["impact"]): StatusIncident => ({
  id,
  name,
  status: "investigating",
  impact,
  shortlink: "#",
  resolved_at: null,
});

const URL = "https://status.example.com";

export const Minor: StoryObj = {
  render: () => (
    <PlatformIncidentBanner
      statusPageUrl={URL}
      incidents={[incident("1", "Elevated build times in us-west-2", "minor")]}
      onDismiss={() => {}}
    />
  ),
};
export const Major: StoryObj = {
  render: () => (
    <PlatformIncidentBanner
      statusPageUrl={URL}
      incidents={[
        incident("1", "Deploys failing in us-west-2", "major"),
        incident("2", "Log search delayed", "minor"),
      ]}
      onDismiss={() => {}}
    />
  ),
};
